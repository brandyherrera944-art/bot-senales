"""
Estrategia del video, SOLO en EUR/USD y SOLO en la mañana de Nueva York (entrada de 8:00 a 12:00 NY).
Datos: ~2 años de velas de 5 min de EUR/USD del banco suizo Dukascopy (mercado real, no OTC).

Se prueban dos versiones (reglas fijadas antes de ver datos; versión bajista, la alcista es espejo):

COMPLETA (como la explica el video):
  1) 1 HORA: confirmación = vela de impulso (cuerpo >= 1.2 ATR) que rompe la estructura
     (cierra bajo el mínimo de 20 velas) y deja un VACÍO; ORDER BLOCK = última vela contraria.
  2) 15 MIN: el precio vuelve a testear el vacío de 1 h (hasta 72 h) y, después de ese test,
     aparece una NUEVA confirmación en 15 min (quiebre + vacío en la misma dirección, en <= 24 h).
     Si el precio cierra más allá del order block de 1 h, se anula.
  3) 5 MIN: el precio testea el vacío de 15 min (hasta 12 h) y una vela de 5 min rompe la mini
     estructura (cierra más allá de las 6 velas anteriores) = ENTRADA (debe ser de 8 a 12 h NY).
  4) STOP: más allá del último nivel de estructura (extremo hecho dentro de la zona).
     TAKE PROFIT: en el punto de estructura (extremo del impulso de 15 min). También se mide 1:2.

SIMPLE (15 min -> 5 min, sin el filtro de 1 hora), para comparar.

Se cobra ~1 pip de spread por operación y se muestra también el resultado sin spread.
Binarias: expiración 15, 30 y 60 min. Entreno = primeros 2/3 del tiempo · Prueba = último 1/3.
"""

import glob
import sys

import numpy as np
import pandas as pd

import bot
import investigar_smc as smc

PIP = 0.0001
ESPERA_TEST_1H = pd.Timedelta(hours=72)
ESPERA_CONF_15 = pd.Timedelta(hours=24)


def en_mañana_ny(ts):
    t = ts.tz_convert("America/New_York")
    return t.weekday() < 5 and 8 <= t.hour < 12


def cargar():
    archivos = sorted(glob.glob("datos/*.csv"))
    if not archivos:
        raise SystemExit("No se encontró el archivo de Dukascopy en datos/")
    df = pd.read_csv(archivos[0])
    df.columns = [c.strip().lower() for c in df.columns]
    df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df = df[df["High"] > df["Low"]]          # quita velas planas (mercado cerrado)
    return df[~df.index.duplicated()].sort_index()


def velas(df, regla):
    g = df.resample(regla, label="left", closed="left")
    v = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                      "Low": g["Low"].min(), "Close": g["Close"].last()}).dropna()
    pc = v["Close"].shift(1)
    tr = pd.concat([v["High"] - v["Low"], (v["High"] - pc).abs(), (v["Low"] - pc).abs()],
                   axis=1).max(axis=1)
    v["atr"] = bot.rma(tr, 14)
    return v


def confirmaciones(v, dur):
    """Quiebre + vacío + order block en el marco v (dur = duración de cada vela)."""
    o, h, l, c, atr = (v[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    out = []
    for j in range(21, len(v) - 1):
        if np.isnan(atr[j - 1]) or abs(c[j] - o[j]) < 1.2 * atr[j - 1]:
            continue
        base = dict(inicio=v.index[j], conocido=v.index[j + 1] + dur, atr=atr[j])
        if c[j] < o[j] and c[j] < l[j - 20:j].min() and h[j + 1] < l[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] > o[k]]
            out.append(dict(base, lado=-1, ob=h[obs[0]] if obs else h[j - 1],
                            borde=h[j + 1], extremo=min(l[j], l[j + 1])))
        elif c[j] > o[j] and c[j] > h[j - 20:j].max() and l[j + 1] > h[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] < o[k]]
            out.append(dict(base, lado=1, ob=l[obs[0]] if obs else l[j - 1],
                            borde=l[j + 1], extremo=max(h[j], h[j + 1])))
    return out


def resultado(h, l, c, m, lado, entrada, stop, objetivo):
    riesgo = abs(entrada - stop)
    for k in range(m + 1, min(m + 1 + smc.MAX_DURACION, len(c))):
        if (lado < 0 and h[k] >= stop) or (lado > 0 and l[k] <= stop):
            return -1.0
        if (lado < 0 and l[k] <= objetivo) or (lado > 0 and h[k] >= objetivo):
            return abs(objetivo - entrada) / riesgo
    k = min(m + smc.MAX_DURACION, len(c) - 1)
    return (c[k] - entrada) * lado / riesgo


def completa(df):
    d = bot.indicadores(df)
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    atr5, idx = d["atr"].values, d.index
    v15 = velas(df, "15min")
    c15, h15, l15 = v15["Close"].values, v15["High"].values, v15["Low"].values
    s1h = confirmaciones(velas(df, "1h"), pd.Timedelta(hours=1))
    s15 = confirmaciones(v15, pd.Timedelta(minutes=15))
    ops, usadas = [], set()
    for z in s1h:
        lado = z["lado"]
        # 2a) test del vacío de 1 h en velas de 15 min
        i0 = v15.index.searchsorted(z["conocido"])
        test = None
        for k in range(i0, len(v15)):
            if v15.index[k] > z["conocido"] + ESPERA_TEST_1H:
                break
            if (lado < 0 and c15[k] > z["ob"]) or (lado > 0 and c15[k] < z["ob"]):
                break
            if (lado < 0 and h15[k] >= z["borde"]) or (lado > 0 and l15[k] <= z["borde"]):
                test = v15.index[k]
                break
        if test is None:
            continue
        # 2b) nueva confirmación de 15 min después del test
        cand = [s for s in s15 if s["lado"] == lado and s["inicio"] >= test
                and s["conocido"] <= test + ESPERA_CONF_15]
        if not cand:
            continue
        s = cand[0]
        tramo = v15[(v15.index >= test) & (v15.index < s["conocido"])]["Close"]
        if (lado < 0 and (tramo > z["ob"]).any()) or (lado > 0 and (tramo < z["ob"]).any()):
            continue
        # 3) entrada en 5 min al testear el vacío de 15 min
        ini = idx.searchsorted(s["conocido"])
        toque = None
        for k in range(ini, min(ini + smc.VENTANA_ESPERA, len(d))):
            if (lado < 0 and c[k] > s["ob"]) or (lado > 0 and c[k] < s["ob"]):
                break
            if toque is None:
                if (lado < 0 and h[k] >= s["borde"]) or (lado > 0 and l[k] <= s["borde"]):
                    toque = k
                continue
            if k - toque > smc.VENTANA_ENTRADA:
                break
            if lado < 0:
                gatillo = c[k] < o[k] and c[k] < l[k - 6:k].min()
            else:
                gatillo = c[k] > o[k] and c[k] > h[k - 6:k].max()
            if not gatillo:
                continue
            if k in usadas or not en_mañana_ny(idx[k]) or k + smc.MAX_DURACION >= len(d):
                break
            ext = h[toque:k + 1].max() if lado < 0 else l[toque:k + 1].min()
            stop = ext - lado * 0.1 * atr5[k]
            riesgo = abs(c[k] - stop)
            if riesgo <= 0 or (s["extremo"] - c[k]) * lado <= 0:
                break                                  # sin recorrido hasta el objetivo
            usadas.add(k)
            costo = PIP / riesgo
            fila = dict(fecha=idx[k], lado=lado, riesgo_pips=round(riesgo / PIP, 1),
                        rr_estructura=round(abs(s["extremo"] - c[k]) / riesgo, 2),
                        R_estructura=resultado(h, l, c, k, lado, c[k], stop, s["extremo"]) - costo,
                        R_1a2=resultado(h, l, c, k, lado, c[k], stop,
                                        c[k] + lado * 2 * riesgo) - costo)
            if (z["extremo"] - c[k]) * lado > 0:           # objetivo en la estructura de 1 h
                fila["rr_1h"] = round(abs(z["extremo"] - c[k]) / riesgo, 2)
                fila["R_estructura_1h"] = resultado(h, l, c, k, lado, c[k], stop, z["extremo"]) - costo
            else:
                fila["rr_1h"], fila["R_estructura_1h"] = np.nan, np.nan
            for e in smc.EXP_BINARIAS:
                dif = (c[k + e] - c[k]) * lado
                fila[f"bin_{e * 5}m"] = np.nan if dif == 0 else float(dif > 0)
            ops.append(fila)
            break
    return pd.DataFrame(ops)


def bloque(x, col):
    x = x.dropna(subset=[col]) if col in x else x.iloc[0:0]
    if x.empty:
        return "sin operaciones"
    sin_costo = (x[col] + 1 / x["riesgo_pips"]).mean()
    b = " · ".join(f"{m}m {100 * x[f'bin_{m}m'].mean():.0f}%" for m in (15, 30, 60))
    return (f"{len(x)} ops · gana {100 * (x[col] > 0).mean():.0f}% · R medio {x[col].mean():+.2f} "
            f"(sin spread {sin_costo:+.2f}) · total {x[col].sum():+.1f}R\n      binarias: {b}")


def main():
    df = cargar()
    print("Velas cargadas:", len(df), df.index[0], "->", df.index[-1])
    corte = df.index[0] + (df.index[-1] - df.index[0]) * 2 / 3
    meses = (df.index[-1] - df.index[0]).days / 30.4

    comp = completa(df)
    bot.en_horario = en_mañana_ny
    simple = pd.DataFrame(smc.operaciones_par("EURUSD", df))
    comp.to_csv("operaciones_smc_ny.csv", index=False)

    lineas = [
        "🔬 <b>Video SMC · EUR/USD · mañana de Nueva York</b>",
        f"Datos reales Dukascopy: {df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d} (~{meses:.0f} meses)",
        "Entrada solo de 8:00 a 12:00 hora NY (7-11 a. m. Colombia) · spread 1 pip.",
        "Gana dinero si R medio > 0 · binarias necesitan ~54 %.\n",
        "<b>COMPLETA 1h→15m→5m, TP en estructura</b>",
    ]
    if not comp.empty:
        lineas.append(f"  (objetivo típico: estructura 15m = {comp['rr_estructura'].median():.1f}x "
                      f"el riesgo · estructura 1h = {comp['rr_1h'].median():.1f}x)")
    for etiqueta, col in (("TP estructura 15m", "R_estructura"),
                          ("TP estructura 1h", "R_estructura_1h"), ("TP 1:2", "R_1a2")):
        lineas.append(f"  [{etiqueta}]")
        for tramo, x in (("Entreno", comp[comp["fecha"] < corte] if not comp.empty else comp),
                         ("Prueba", comp[comp["fecha"] >= corte] if not comp.empty else comp)):
            lineas.append(f"  {tramo}: {bloque(x, col)}")
    lineas.append("\n<b>SIMPLE 15m→5m, TP 1:2</b> (para comparar)")
    for tramo, x in (("Entreno", simple[simple["fecha"] < corte] if not simple.empty else simple),
                     ("Prueba", simple[simple["fecha"] >= corte] if not simple.empty else simple)):
        lineas.append(f"  {tramo}: {bloque(x, 'R_1a2')}")
    if not comp.empty:
        comp["tri"] = pd.to_datetime(comp["fecha"], utc=True).dt.tz_localize(None).dt.to_period("Q")
        t = comp.groupby("tri")["R_estructura"].agg(["count", "sum"])
        lineas.append("\n<b>Completa, R por trimestre:</b> " +
                      ", ".join(f"{p}: {s:+.1f}R ({int(n)})" for p, (n, s) in t.iterrows()))
    lineas.append("\nR = lo que arriesgas por operación (ej.: +1R = ganaste lo que arriesgaste).")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)


if __name__ == "__main__":
    sys.exit(main())
