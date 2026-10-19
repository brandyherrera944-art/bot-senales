"""
Prueba honesta de la estrategia del video: QUIEBRE + VACÍO (Fair Value Gap) + ORDER BLOCK,
con confirmación en 5 minutos. (Se describe la versión bajista; la alcista es el espejo.)

  1) 15 min: vela de impulso fuerte (cuerpo >= 1.2 ATR) que cierra por debajo del mínimo de las
     últimas 20 velas  -> QUIEBRE de estructura.
  2) Ese impulso deja un VACÍO (FVG): el máximo de la vela siguiente queda por debajo del mínimo
     de la vela anterior. El ORDER BLOCK es la última vela alcista antes del impulso.
  3) Se espera (hasta 12 h) que el precio vuelva a entrar al VACÍO. Si cierra por encima del
     order block, la idea se anula.
  4) 5 min: dentro de la zona, una vela bajista que cierra por debajo del mínimo de las 6 velas
     anteriores (mini quiebre) = ENTRADA. Stop arriba del máximo hecho en la zona.
Variantes fijadas antes de ver datos:  V1 = lo anterior · V2 = V1 + tendencia de 1 h a favor.
Se mide de dos formas:
  * Trading con stop y objetivo 1:2 y 1:3 (como en el video), descontando ~1 pip de spread.
  * Opciones binarias con expiración 15, 30 y 60 min (empates no cuentan).
~59 días de velas de 5 min. Entreno = primeros 2/3 · Prueba = último 1/3.
"""

import math
import sys

import numpy as np
import pandas as pd

import bot

LOOKBACK_QUIEBRE = 20
VENTANA_ESPERA = 144      # velas de 5 min (12 h) para volver al vacío
VENTANA_ENTRADA = 24      # velas de 5 min (2 h) tras tocar la zona para confirmar
MAX_DURACION = 288        # 24 h máximo por operación
EXP_BINARIAS = [3, 6, 12]  # 15, 30, 60 min


def velas_15m(df):
    g = df.resample("15min", label="left", closed="left")
    v = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                      "Low": g["Low"].min(), "Close": g["Close"].last()}).dropna()
    pc = v["Close"].shift(1)
    tr = pd.concat([v["High"] - v["Low"], (v["High"] - pc).abs(), (v["Low"] - pc).abs()],
                   axis=1).max(axis=1)
    v["atr"] = bot.rma(tr, 14)
    return v


def setups(v):
    """Lista de setups detectados en 15 min: dict(lado, conocido, ob, tope, base, atr15)."""
    o, h, l, c, atr = (v[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    out = []
    for j in range(LOOKBACK_QUIEBRE + 1, len(v) - 1):
        cuerpo = abs(c[j] - o[j])
        if np.isnan(atr[j - 1]) or cuerpo < 1.2 * atr[j - 1]:
            continue
        conocido = v.index[j + 1] + pd.Timedelta(minutes=15)   # el vacío se confirma al cerrar j+1
        if c[j] < o[j] and c[j] < l[j - LOOKBACK_QUIEBRE:j].min() and h[j + 1] < l[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] > o[k]]
            ob = h[obs[0]] if obs else h[j - 1]
            out.append(dict(lado=-1, conocido=conocido, ob=ob, borde=h[j + 1], atr15=atr[j]))
        elif c[j] > o[j] and c[j] > h[j - LOOKBACK_QUIEBRE:j].max() and l[j + 1] > h[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] < o[k]]
            ob = l[obs[0]] if obs else l[j - 1]
            out.append(dict(lado=1, conocido=conocido, ob=ob, borde=l[j + 1], atr15=atr[j]))
    return out


def resultado_rr(h, l, c, m, lado, entrada, stop, rr):
    """R obtenido con objetivo rr veces el riesgo (si stop y objetivo caen en la misma vela: pérdida)."""
    riesgo = abs(entrada - stop)
    obj = entrada + lado * rr * riesgo
    for k in range(m + 1, min(m + 1 + MAX_DURACION, len(c))):
        if (lado < 0 and h[k] >= stop) or (lado > 0 and l[k] <= stop):
            return -1.0
        if (lado < 0 and l[k] <= obj) or (lado > 0 and h[k] >= obj):
            return float(rr)
    k = min(m + MAX_DURACION, len(c) - 1)
    return (c[k] - entrada) * lado / riesgo


def operaciones_par(par, df):
    df = bot.indicadores(df)
    v = velas_15m(df)
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    atr5, t1h = df["atr"].values, df["tend_1h"].values
    idx = df.index
    spread = 0.01 if par.endswith("JPY") else 0.0001
    ops, usadas = [], set()
    for s in setups(v):
        ini = idx.searchsorted(s["conocido"])
        lado, ob, borde = s["lado"], s["ob"], s["borde"]
        toque = None
        for k in range(ini, min(ini + VENTANA_ESPERA, len(df))):
            if (lado < 0 and c[k] > ob) or (lado > 0 and c[k] < ob):
                break                                   # se anuló el order block
            if toque is None:
                if (lado < 0 and h[k] >= borde) or (lado > 0 and l[k] <= borde):
                    toque = k
                continue
            if k - toque > VENTANA_ENTRADA:
                break
            ext = h[toque:k + 1].max() if lado < 0 else l[toque:k + 1].min()
            if lado < 0:
                gatillo = c[k] < o[k] and c[k] < l[k - 6:k].min()
            else:
                gatillo = c[k] > o[k] and c[k] > h[k - 6:k].max()
            if not gatillo:
                continue
            if k in usadas or not bot.en_horario(idx[k]) or k + MAX_DURACION >= len(df):
                break
            stop = ext + lado * -0.1 * atr5[k]          # un poco más allá del extremo de la zona
            riesgo = abs(c[k] - stop)
            if riesgo <= 0 or riesgo > 3 * s["atr15"]:
                break
            usadas.add(k)
            costo = spread / riesgo
            fila = dict(par=par, fecha=idx[k], lado=lado, tend_1h_a_favor=bool(t1h[k] * lado > 0),
                        riesgo_pips=round(riesgo / spread, 1),
                        R_1a2=resultado_rr(h, l, c, k, lado, c[k], stop, 2) - costo,
                        R_1a3=resultado_rr(h, l, c, k, lado, c[k], stop, 3) - costo)
            for e in EXP_BINARIAS:
                dif = (c[k + e] - c[k]) * lado
                fila[f"bin_{e * 5}m"] = np.nan if dif == 0 else float(dif > 0)
            ops.append(fila)
            break
    return ops


def resumen(x):
    if x.empty:
        return "sin operaciones"
    n = len(x)
    p2 = 100 * (x["R_1a2"] > 0).mean()
    p3 = 100 * (x["R_1a3"] > 0).mean()
    b = " · ".join(f"{m}m {100 * x[f'bin_{m}m'].mean():.0f}%" for m in (15, 30, 60))
    return (f"{n} ops · 1:2 gana {p2:.0f}% (R medio {x['R_1a2'].mean():+.2f}) · "
            f"1:3 gana {p3:.0f}% (R medio {x['R_1a3'].mean():+.2f})\n      binarias: {b}")


def main():
    datos = bot.descargar(bot.PARES, periodo="59d")
    todas = []
    for par, df in datos.items():
        if len(df) > 2000:
            todas += operaciones_par(par, df)
    r = pd.DataFrame(todas)
    r.to_csv("operaciones_smc.csv", index=False)
    if r.empty:
        bot.enviar("🔬 Prueba SMC (order block + vacío): no se encontraron operaciones.")
        return
    corte = r["fecha"].min() + (r["fecha"].max() - r["fecha"].min()) * 2 / 3
    lineas = ["🔬 <b>Prueba: Quiebre + Vacío + Order Block (video)</b>",
              "15 min para la zona, 5 min para entrar · ~59 días · 7 pares",
              "Para ganar: con 1:2 necesitas >33 % y R medio > 0; con 1:3 >25 %; "
              "en binarias ~54 %.\n"]
    for nombre, filtro in (("V1 (como el video)", r.index == r.index),
                           ("V2 (+ tendencia 1 h a favor)", r["tend_1h_a_favor"])):
        x = r[filtro]
        lineas.append(f"<b>{nombre}</b>")
        lineas.append(f"  Entreno: {resumen(x[x['fecha'] < corte])}")
        lineas.append(f"  Prueba:  {resumen(x[x['fecha'] >= corte])}\n")
    por_par = r.groupby("par")["R_1a2"].agg(["count", "mean"])
    lineas.append("<b>Por par (1:2, todo el periodo):</b> " + ", ".join(
        f"{p} {int(n)} ops R{m:+.2f}" for p, (n, m) in por_par.iterrows()))
    lineas.append("\nR medio = ganancia promedio por operación en múltiplos del riesgo "
                  "(ya descontado ~1 pip de spread).")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)


if __name__ == "__main__":
    sys.exit(main())
