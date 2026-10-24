"""
Prueba del modelo AMD (Acumulación - Manipulación - Distribución, "Power of 3" de ICT/SMC).
Reglas fijadas ANTES de ver los datos:

  A) Acumulación: rango de la sesión de Asia, de 00:00 a 07:00 UTC (7 p. m. a 2 a. m. Colombia).
  M) Manipulación: de 07:00 a 10:00 UTC (apertura de Londres) el precio pasa el máximo (o el mínimo)
     de Asia y luego una vela de 5 min vuelve a cerrar DENTRO del rango (trampa / barrida de stops).
  D) Distribución: se entra al cierre de esa vela hacia el lado contrario de la trampa
     (si barrió el máximo -> venta / PUT; si barrió el mínimo -> compra / CALL).
  Una operación por par y por día (la primera trampa). Si barre los dos lados antes, no se opera.

  Variantes: AMD · AMD + rango estrecho (rango de Asia menor que la mediana de los 20 días anteriores)
             · Azar (control): promedio de comprar y vender en el mismo momento, con el mismo stop.
  Se mide:
   - Binarias: expiración 15, 30 y 60 min (empates no cuentan). Pago 85 % -> hace falta > 54,1 %.
   - Forex: stop 1 pip más allá del extremo de la trampa (mínimo 5 pips), spread incluido.
       TP "lado opuesto" = el otro extremo del rango de Asia · TP "1,5 R". Cierre a las 20:00 UTC.
       Qué se toca primero se revisa minuto a minuto (si caen en el mismo minuto, cuenta el stop).
Datos: velas de 1 min de HistData (enero 2024 a septiembre 2026), 7 pares del bot.
Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
"""

import os
import sys
import time

import numpy as np
import pandas as pd

import bot
import investigar_forex as fx      # spreads, pip, resumen
import investigar_pa as ip         # descarga de HistData (velas de 1 min)

PARES = [p.strip().upper() for p in os.getenv("PARES_AMD", ",".join(bot.PARES)).split(",") if p.strip()]
EXPIRACIONES = [15, 30, 60]
PAGO = 0.85
EQUILIBRIO = 100 / (1 + PAGO)
META = 56.0
MIN = 60 * 10**9
HORA = 60 * MIN
VARIANTES = {"AMD": "AMD", "EST": "AMD + rango estrecho", "AZAR": "Azar (control)"}


def entradas_del_par(m1):
    """Busca cada día la trampa de Londres sobre el rango de Asia. Devuelve una fila por entrada."""
    T1, H1, L1 = m1["T"], m1["H"], m1["L"]
    d5 = m1["d5"]
    T5, H5, L5, C5 = d5.index.as_unit("ns").asi8, d5["h"].values, d5["l"].values, d5["c"].values
    dias = m1["idx"].normalize().unique()
    filas = []
    for dia in dias:
        if dia.dayofweek >= 5:
            continue
        a0 = dia.value
        a, b = np.searchsorted(T1, a0, "left"), np.searchsorted(T1, a0 + 7 * HORA, "left")
        if b - a < 200:                                # sin datos suficientes de Asia (festivo)
            continue
        alto, bajo = H1[a:b].max(), L1[a:b].min()
        j0, j1 = np.searchsorted(T5, a0 + 7 * HORA, "left"), np.searchsorted(T5, a0 + 10 * HORA, "left")
        barrido, ext = 0, None
        for j in range(j0, j1):
            if barrido == 0:
                if H5[j] > alto and L5[j] < bajo:
                    break                              # barrió los dos lados en la misma vela
                if H5[j] > alto:
                    barrido, ext = 1, H5[j]
                elif L5[j] < bajo:
                    barrido, ext = -1, L5[j]
            elif (barrido == 1 and L5[j] < bajo) or (barrido == -1 and H5[j] > alto):
                break                                  # barrió el otro lado: se cancela
            if barrido == 1:
                ext = max(ext, H5[j])
                if C5[j] < alto:
                    if C5[j] > bajo:
                        filas.append(dict(dia=dia, t=T5[j] + 5 * MIN, lado=-1, precio=C5[j], ext=ext,
                                          alto=alto, bajo=bajo))
                    break
            elif barrido == -1:
                ext = min(ext, L5[j])
                if C5[j] > bajo:
                    if C5[j] < alto:
                        filas.append(dict(dia=dia, t=T5[j] + 5 * MIN, lado=1, precio=C5[j], ext=ext,
                                          alto=alto, bajo=bajo))
                    break
    return filas


def binaria(m1, t, lado, precio, e):
    """Resultado de una binaria que entra en t (cierre de vela) y vence e minutos después."""
    T1, C1 = m1["T"], m1["C"]
    k = np.searchsorted(T1, t + (e - 1) * MIN, "left")   # minuto que cierra justo al vencer
    if k >= len(T1) or T1[k] != t + (e - 1) * MIN:
        return np.nan
    dif = (C1[k] - precio) * lado
    return np.nan if dif == 0 else float(dif > 0)


def forex(m1, par, f, lado, tp_modo):
    """R de una operación con stop tras la trampa y TP al otro lado del rango o a 1,5 R."""
    T1, O1, H1, L1, C1 = m1["T"], m1["O"], m1["H"], m1["L"], m1["C"]
    p, spr = fx.pip(par), fx.SPREAD_PIPS.get(par, 1.5) * fx.pip(par)
    ent = f["precio"] + spr if lado > 0 else f["precio"]
    # stop 1 pip más allá del extremo de la trampa (mínimo 5 pips); el azar usa la misma distancia
    riesgo = max(abs(f["precio"] - f["ext"]) + spr + p, 5 * p)
    stop = ent - lado * riesgo
    if tp_modo == "opuesto":
        tp = f["alto"] if lado > 0 else f["bajo"] + spr
        if (tp - ent) * lado <= 0:
            return np.nan
    else:
        tp = ent + lado * 1.5 * riesgo
    a = np.searchsorted(T1, f["t"], "left")
    b = np.searchsorted(T1, f["dia"].value + 20 * HORA, "left")
    if b <= a:
        return np.nan
    h, l, o = H1[a:b], L1[a:b], O1[a:b]
    if lado > 0:
        toca_sl, toca_tp = l <= stop, h >= tp
    else:
        toca_sl, toca_tp = h + spr >= stop, l + spr <= tp
    k_sl = np.argmax(toca_sl) if toca_sl.any() else len(h)
    k_tp = np.argmax(toca_tp) if toca_tp.any() else len(h)
    if k_sl < len(h) and k_sl <= k_tp:
        apertura = o[k_sl] if lado > 0 else o[k_sl] + spr
        salida = min(stop, apertura) if lado > 0 else max(stop, apertura)
    elif k_tp < len(h):
        salida = tp
    else:
        salida = C1[b - 1] if lado > 0 else C1[b - 1] + spr
    return (salida - ent) * lado / riesgo


def main():
    filas, inicio, fin = [], None, None
    for par in PARES:
        t0 = time.time()
        df1 = ip.velas_1m(par)
        if df1 is None or len(df1) < 100000:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        g = df1.resample("5min", label="left", closed="left")
        d5 = pd.DataFrame({"h": g["high"].max(), "l": g["low"].min(), "c": g["close"].last()}).dropna()
        m1 = dict(idx=df1.index, T=df1.index.as_unit("ns").asi8, O=df1["open"].values,
                  H=df1["high"].values, L=df1["low"].values, C=df1["close"].values, d5=d5)
        ent = entradas_del_par(m1)
        # mediana del rango de Asia de los 20 días anteriores (sin contar el día de hoy)
        todos = df1[(df1.index.hour < 7) & (df1.index.dayofweek < 5)]
        r_dia = (todos["high"].groupby(todos.index.normalize()).max()
                 - todos["low"].groupby(todos.index.normalize()).min())
        mediana = r_dia.rolling(20, min_periods=10).median().shift(1)
        for f in ent:
            med = mediana.get(f["dia"], np.nan)
            estrecho = med == med and (f["alto"] - f["bajo"]) < med
            for v, lado in (("AMD", f["lado"]), ("EST", f["lado"] if estrecho else 0)):
                if not lado:
                    continue
                fila = dict(par=par, variante=v, fecha=pd.Timestamp(f["t"], tz="UTC"))
                for e in EXPIRACIONES:
                    fila[f"b{e}"] = binaria(m1, f["t"], lado, f["precio"], e)
                fila["R_opuesto"] = forex(m1, par, f, lado, "opuesto")
                fila["R_15"] = forex(m1, par, f, lado, "1.5R")
                filas.append(fila)
            # control: lo que da en promedio entrar a cualquier lado en ese mismo momento
            filas.append(dict(par=par, variante="AZAR", fecha=pd.Timestamp(f["t"], tz="UTC"),
                              R_opuesto=np.nan,
                              R_15=np.nanmean([forex(m1, par, f, 1, "1.5R"), forex(m1, par, f, -1, "1.5R")])))
        inicio = df1.index[0] if inicio is None else min(inicio, df1.index[0])
        fin = df1.index[-1] if fin is None else max(fin, df1.index[-1])
        print(f"{par}: {len(ent)} días con trampa AMD ({time.time() - t0:.0f} s)", flush=True)
        del df1, m1
    if not filas:
        bot.enviar("⚠️ Prueba AMD: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.DataFrame(filas)
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte
    ops["tri"] = ops["fecha"].dt.tz_localize(None).dt.to_period("Q")
    ops.to_csv("operaciones_amd.csv", index=False)

    def pct(x):
        x = x.dropna()
        if not len(x):
            return "sin ops", None
        p = 100 * x.mean()
        return f"{p:.1f}% de {len(x)} (±{196 * np.sqrt(x.mean() * (1 - x.mean()) / len(x)):.1f})", p

    lineas = [
        "🔬 <b>Modelo AMD · Asia → trampa en Londres → movimiento real</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares.",
        "Rango de Asia 00-07 UTC (7 p. m.-2 a. m. Colombia) · trampa de 07 a 10 UTC (2-5 a. m.) ·",
        "entrada cuando una vela de 5 min vuelve a cerrar dentro del rango, al lado contrario.",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
        f"\n<b>Binarias</b> (pago 85 %: no pierdes sobre {EQUILIBRIO:.1f} %, meta {META:.0f} %)",
    ]
    aprob = []
    for v, nombre in VARIANTES.items():
        if v == "AZAR":
            lineas.append("• Al azar (moneda) daría 50 %.")
            continue
        x = ops[ops["variante"] == v]
        partes = []
        for e in EXPIRACIONES:
            tot, _ = pct(x[f"b{e}"])
            _, pe = pct(x.loc[x["entreno"], f"b{e}"])
            pr_txt, pp = pct(x.loc[~x["entreno"], f"b{e}"])
            partes.append(f"   {e} min: {tot} · prueba {pr_txt}")
            if v != "AZAR" and pe and pp and pe >= META and pp >= META:
                aprob.append(f"{nombre} binaria {e} min")
        lineas.append(f"• <b>{nombre}</b>\n" + "\n".join(partes))
    lineas.append("\n<b>Forex</b> (R por operación, spread incluido; R = lo que arriesgas)")
    for v, nombre in VARIANTES.items():
        x = ops[ops["variante"] == v]
        for col, txt in (("R_opuesto", "TP lado opuesto"), ("R_15", "TP 1,5 R")):
            y = x.dropna(subset=[col]).rename(columns={col: "R"})
            if y.empty:
                continue
            t, e, p = fx.resumen(y), fx.resumen(y[y["entreno"]]), fx.resumen(y[~y["entreno"]])
            q = y.groupby("tri")["R"].sum()
            pos = int((q > 0).sum())
            ganan = "" if v == "AZAR" else f"{t['ganadas']:.0f}% ganan · "
            lineas.append(f"• {nombre} · {txt}: {t['n']} ops · {ganan}"
                          f"<b>{t['r_op']:+.3f} R/op</b> · entreno {e['r_op']:+.3f} · prueba {p['r_op']:+.3f}"
                          f" · trimestres + {pos}/{len(q)}")
            if v != "AZAR" and e["r_op"] > 0 and p["r_op"] > 0 and pos >= len(q) / 2 and p["n"] >= 100:
                aprob.append(f"{nombre} forex {txt}")
    lineas.append("\n<b>Por par en la prueba</b> (AMD: binaria 30 min · forex TP lado opuesto)")
    x = ops[(ops["variante"] == "AMD") & ~ops["entreno"]]
    lineas.append(" · ".join(f"{bot.nombre_par(k)} {100 * y['b30'].mean():.0f}% / {y['R_opuesto'].mean():+.2f} R"
                             for k, y in x.groupby("par")))
    if aprob:
        lineas.append("\n✅ Pasan en entreno Y en prueba: " + ", ".join(aprob))
    else:
        lineas.append("\n❌ Ninguna variante de AMD pasa en entreno Y en prueba.")
    lineas.append("Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
