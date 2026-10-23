"""
Prueba de la estrategia "EMA + Ruptura de Estructura (RE)" (velas de 1 h y de 4 h), para el bot forex.
Reglas fijadas ANTES de ver los datos:

  Tendencia: bajista si la EMA 50 está bajo la EMA 200; alcista si está encima.
  Estructura: máximos y mínimos pivote (5 velas a cada lado; se conocen 5 velas después).
  Ruptura de estructura (RE): en tendencia bajista, una vela cierra por debajo del último mínimo
     confirmado; eso "arma" una venta (en alcista, al revés, cierre sobre el último máximo).
  Entrada: con la venta armada, el precio vuelve a tocar la EMA 50 y la vela cierra debajo de ella
     (rechazo). Se vende al cierre. Después hace falta una nueva RE para la siguiente entrada.
     Si la estructura se rompe al revés o la tendencia cambia, se desarma.

  Dos formas de salir:
   a) SL 1 ATR y TP 1,5 ATR (cierre a las 24 velas si no toca ninguno).
   b) Por estructura: stop en el último máximo confirmado (venta) o mínimo (compra), entre 0,5 y
      3 ATR de la entrada; el stop se va moviendo con cada nuevo máximo más bajo (o mínimo más alto)
      y se sale cuando el precio lo toca, "hasta que se rompa la estructura en dirección opuesta".
      Máximo 120 velas.
  Control: entradas al azar con la misma frecuencia y las mismas salidas.

Qué se toca primero se revisa con velas de 1 min. Spread incluido según el par (1 a 1,8 pips).
Resultado en R (R = lo que se arriesga). Una operación abierta a la vez por par y variante.
Datos: velas de 1 min de HistData (enero 2024 a septiembre 2026), 7 pares del bot.
Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
"""

import os
import sys
import time

import numpy as np
import pandas as pd

import bot
import investigar_forex as fx      # spreads, resumen y funciones comunes
import investigar_pa as ip         # descarga de HistData (velas de 1 min)

PARES = [p.strip().upper() for p in os.getenv("PARES_FX", ",".join(bot.PARES)).split(",") if p.strip()]
TEMPORALIDADES = {"1h": 1, "4h": 4}
PIV = 5
MAX_VELAS_ATR, MAX_VELAS_EST = 24, 120
MIN_OPS_PRUEBA = 60
MIN = 60 * 10**9
HORA = 60 * MIN
VARIANTES = ["Azar · SL/TP", "EMA+RE · SL/TP", "Azar · estructura", "EMA+RE · estructura"]


def velas(m1, horas):
    g = m1.resample(f"{horas}h", label="left", closed="left")
    df = pd.DataFrame({"Open": g["open"].first(), "High": g["high"].max(),
                       "Low": g["low"].min(), "Close": g["close"].last()}).dropna()
    return df[df["High"] > df["Low"]]


def ultimos_pivotes(df):
    """Para cada vela: último máximo y último mínimo pivote ya confirmados al cierre de esa vela."""
    h, l = df["High"], df["Low"]
    w = 2 * PIV + 1
    es_alto = (h.rolling(w, center=True).max() == h).values
    es_bajo = (l.rolling(w, center=True).min() == l).values
    n = len(df)
    alto, bajo = np.full(n, np.nan), np.full(n, np.nan)
    a = b = np.nan
    for t in range(n):
        j = t - PIV                                   # el pivote en j se confirma al cierre de j + PIV
        if j >= 0:
            if es_alto[j]:
                a = h.iat[j]
            if es_bajo[j]:
                b = l.iat[j]
        alto[t], bajo[t] = a, b
    return alto, bajo


def preparar(df):
    df = fx.indicadores(df)
    df["e50"] = fx.ema(df["Close"], 50)
    df["alto"], df["bajo"] = ultimos_pivotes(df)
    return df


def senales_ema_re(df):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    e50, e200, alto, bajo = df["e50"].values, df["e200"].values, df["alto"].values, df["bajo"].values
    lado = np.zeros(len(df), int)
    venta = compra = False
    for t in range(251, len(df)):
        baja, sube = e50[t] < e200[t], e50[t] > e200[t]
        nueva = False
        if c[t] > alto[t - 1]:                        # RE hacia arriba: arma compra, desarma venta
            venta, compra = False, sube
            nueva = compra
        if c[t] < bajo[t - 1]:                        # RE hacia abajo: arma venta, desarma compra
            compra, venta = False, baja
            nueva = venta
        if not baja:
            venta = False
        if not sube:
            compra = False
        if nueva:                                     # la entrada llega en una vela posterior
            continue
        if venta and h[t] >= e50[t] and c[t] < e50[t]:
            lado[t], venta = -1, False
        elif compra and l[t] <= e50[t] and c[t] > e50[t]:
            lado[t], compra = 1, False
    return lado


def simular(par, nombre, df, lado, m, horas, salida):
    T1, O1, H1, L1, C1 = m["T"], m["O"], m["H"], m["L"], m["C"]
    spr = fx.SPREAD_PIPS.get(par, 1.5) * fx.pip(par)
    dur = horas * HORA
    t0 = df.index.as_unit("ns").asi8
    close, atr = df["Close"].values, df["atr"].values
    alto, bajo = df["alto"].values, df["bajo"].values
    libre, filas = -1, []
    for i in np.flatnonzero(lado):
        ent_t = t0[i] + dur
        if ent_t < libre or not atr[i] > 0 or i + 1 >= len(df):
            continue
        s = lado[i]
        ent = close[i] + spr if s > 0 else close[i]
        if salida == "atr":
            stop, tp = ent - s * atr[i], ent + s * 1.5 * atr[i]
            max_velas = MAX_VELAS_ATR
        else:
            ref = bajo[i] if s > 0 else alto[i]
            dist = (ent - ref) * s if ref == ref else atr[i]
            dist = min(max(dist, 0.5 * atr[i]), 3 * atr[i])
            stop, tp = ent - s * dist, None
            max_velas = MAX_VELAS_EST
        riesgo = abs(ent - stop)
        res, fin_t, ultimo = None, None, None
        for k in range(i + 1, min(i + 1 + max_velas, len(df))):
            if salida == "est":                       # mueve el stop con la estructura (solo a favor)
                nuevo = bajo[k - 1] if s > 0 else alto[k - 1]
                if nuevo == nuevo and (nuevo - stop) * s > 0:
                    stop = nuevo
            a = np.searchsorted(T1, t0[k], "left")
            b = np.searchsorted(T1, t0[k] + dur, "left")
            if b <= a:
                continue
            ultimo = b - 1
            ab, ah, al = (O1[a:b], H1[a:b], L1[a:b])
            if s > 0:                                  # compra: sale al bid
                toca_sl = al <= stop
                toca_tp = ah >= tp if tp is not None else np.zeros(len(ah), bool)
            else:                                      # venta: sale al ask
                toca_sl = ah + spr >= stop
                toca_tp = al + spr <= tp if tp is not None else np.zeros(len(ah), bool)
            k_sl = np.argmax(toca_sl) if toca_sl.any() else len(ab)
            k_tp = np.argmax(toca_tp) if toca_tp.any() else len(ab)
            if k_sl < len(ab) and k_sl <= k_tp:
                apertura = ab[k_sl] if s > 0 else ab[k_sl] + spr
                precio = min(stop, apertura) if s > 0 else max(stop, apertura)   # si abre con hueco
                res, fin_t = (precio - ent) * s / riesgo, T1[a + k_sl]
                break
            if k_tp < len(ab):
                res, fin_t = 1.5, T1[a + k_tp]
                break
        if res is None:                               # no tocó nada: cierre a mercado al final
            if ultimo is None:
                continue
            precio = C1[ultimo] if s > 0 else C1[ultimo] + spr
            res, fin_t = (precio - ent) * s / riesgo, T1[ultimo]
        libre = fin_t + MIN
        filas.append((par, nombre, df.index[i], float(res)))
    return filas


def main():
    rng = np.random.default_rng(11)
    filas, inicio, fin = [], None, None
    for par in PARES:
        t_ini = time.time()
        m1 = ip.velas_1m(par)
        if m1 is None or len(m1) < 100000:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        m = dict(T=m1.index.as_unit("ns").asi8, O=m1["open"].values, H=m1["high"].values,
                 L=m1["low"].values, C=m1["close"].values)
        for tf, horas in TEMPORALIDADES.items():
            df = preparar(velas(m1, horas))
            lado = senales_ema_re(df)
            entre_semana = (df.index + pd.Timedelta(hours=horas)).dayofweek < 5
            lado = np.where(entre_semana, lado, 0)
            frec = np.mean(lado != 0)
            azar = np.where((rng.random(len(df)) < frec) & entre_semana & (np.arange(len(df)) > 250),
                            rng.choice([-1, 1], len(df)), 0)
            for nombre, l_, salida in ((VARIANTES[0], azar, "atr"), (VARIANTES[1], lado, "atr"),
                                       (VARIANTES[2], azar, "est"), (VARIANTES[3], lado, "est")):
                filas += [(f[0], f"{tf} · {f[1]}", f[2], f[3]) for f in simular(par, nombre, df, l_, m, horas, salida)]
            ini = df.index[251]
            inicio = ini if inicio is None else min(inicio, ini)
            fin = df.index[-1] if fin is None else max(fin, df.index[-1])
            print(f"{par} {tf}: {len(df)} velas · {np.count_nonzero(lado)} entradas EMA+RE "
                  f"({time.time() - t_ini:.0f} s)", flush=True)
        del m1, m
    if not filas:
        bot.enviar("⚠️ Prueba EMA + RE: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.DataFrame(filas, columns=["par", "variante", "fecha", "R"]).sort_values("fecha")
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte
    ops["tri"] = ops["fecha"].dt.tz_localize(None).dt.to_period("Q")

    tabla = []
    for (par, v), x in ops.groupby(["par", "variante"]):
        e, p = fx.resumen(x[x["entreno"]]), fx.resumen(x[~x["entreno"]])
        tabla.append(dict(par=par, variante=v, ops_entreno=e["n"], r_op_entreno=round(e.get("r_op", 0), 3),
                          ops_prueba=p["n"], r_op_prueba=round(p.get("r_op", 0), 3)))
    pd.DataFrame(tabla).to_csv("investigacion_ema_re.csv", index=False)

    lineas = [
        "🔬 <b>EMA 50/200 + Ruptura de Estructura · velas de 1 h y 4 h</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares · spread incluido.",
        "Salidas: SL/TP = SL 1 ATR y TP 1,5 ATR · estructura = stop en el último máximo/mínimo, que se va moviendo.",
        "R = lo que arriesgas por operación. Azar = entradas al azar con las mismas salidas (para comparar).",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
    ]
    aprobadas = []
    for tf in TEMPORALIDADES:
        lineas.append(f"\n<b>Velas de {tf}</b>")
        for v in VARIANTES:
            nombre = f"{tf} · {v}"
            x = ops[ops["variante"] == nombre]
            t, e, p = fx.resumen(x), fx.resumen(x[x["entreno"]]), fx.resumen(x[~x["entreno"]])
            if not t["n"]:
                lineas.append(f"• {v}: sin operaciones")
                continue
            q = x.groupby("tri")["R"].sum()
            pos = int((q > 0).sum())
            lineas.append(f"• <b>{v}</b>: {t['n']} ops · {t['ganadas']:.0f}% ganan · <b>{t['r_op']:+.3f} R/op</b>"
                          f" · entreno {e['r_op']:+.3f} · prueba {p['r_op']:+.3f} ({p['n']})"
                          f" · trimestres + {pos}/{len(q)} · caída {t['caida']:.0f} R")
            if v.startswith("EMA") and e["r_op"] > 0 and p["r_op"] > 0 and p["n"] >= MIN_OPS_PRUEBA \
                    and pos >= len(q) / 2:
                aprobadas.append((p["r_op"], nombre))
    lineas.append("\n<b>Por par en la prueba</b> (EMA+RE, R por operación)")
    for tf in TEMPORALIDADES:
        for v in VARIANTES[1::2]:
            x = ops[(ops["variante"] == f"{tf} · {v}") & ~ops["entreno"]]
            pp = x.groupby("par")["R"].mean()
            lineas.append(f"{tf} {v.split(' · ')[1]}: " + " · ".join(f"{k[:3]}/{k[3:]} {r:+.2f}" for k, r in pp.items()))
    if aprobadas:
        lineas.append("\n✅ Pasan (positivas en entreno Y prueba, ≥60 ops en prueba, mitad de trimestres "
                      "positivos): " + ", ".join(n for _, n in sorted(aprobadas, reverse=True)))
    else:
        lineas.append("\n❌ Ninguna variante de EMA + RE pasa: no gana en entreno Y en prueba de forma constante.")
    lineas.append("Spreads y salidas son aproximados. Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
