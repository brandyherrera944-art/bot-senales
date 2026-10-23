"""
Prueba para el BOT FOREX (velas de 1 hora, con stop loss y take profit).
Reglas fijadas ANTES de ver los datos:

  0) Azar (control): entradas al azar con la misma frecuencia que el cruce de EMAs.
     Sirve para ver qué da el SL/TP por sí solo, sin ninguna estrategia.
  1) Cruce de EMAs: la EMA 9 cruza la EMA 21 a favor de la EMA 200
     (compra si el precio está sobre la EMA 200; venta si está debajo).
  2) 1 + filtro de volatilidad: no se opera si el ATR(14) está en el 20 % más alto de los
     últimos 20 días, ni si la vela de la señal mide más de 2 ATR (vela de noticia).
  3) RSI2: compra si el precio está sobre la EMA 200 y el RSI(2) < 10; venta si está debajo y > 90.
  4) 3 + filtro de volatilidad.

Cada operación: entrada al cierre de la vela de 1 h · stop loss a 1 ATR · take profit a 1,5 ATR
· si no toca ninguno en 24 h, se cierra a precio de mercado. Qué se toca primero se revisa con
velas de 1 minuto (si ambos caen en el mismo minuto, se cuenta el stop loss).
Spread incluido según el par. Una operación abierta a la vez por par y estrategia.
Resultado en R: +1,5 R si toca el TP, -1 R si toca el SL (R = lo que se arriesga).
Para no perder hace falta ganar más del 40 % de las veces (antes del spread).

Datos: velas de 1 min de HistData (enero 2024 a septiembre 2026), los 7 pares del bot.
Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
"""

import os
import sys
import time

import numpy as np
import pandas as pd

import bot
import investigar_pa as ip          # descarga de HistData (velas de 1 min)

PARES = [p.strip().upper() for p in os.getenv("PARES_FX", ",".join(bot.PARES)).split(",") if p.strip()]
SPREAD_PIPS = {"EURUSD": 1.0, "GBPUSD": 1.5, "USDJPY": 1.2, "AUDUSD": 1.2,
               "USDCAD": 1.8, "USDCHF": 1.8, "NZDUSD": 1.8}
SL_ATR, TP_ATR, MAX_HORAS = 1.0, 1.5, 24
MIN_OPS_PRUEBA = 100
VARIANTES = {0: "0) Azar (control)", 1: "1) Cruce EMA 9/21 + EMA 200", 2: "2) Cruce EMAs + volatilidad",
             3: "3) RSI2 + EMA 200", 4: "4) RSI2 + volatilidad"}
MIN = 60 * 10**9
HORA = 60 * MIN


def pip(par):
    return 0.01 if par.endswith("JPY") else 0.0001


def a_1h(m1):
    g = m1.resample("1h", label="left", closed="left")
    df = pd.DataFrame({"Open": g["open"].first(), "High": g["high"].max(),
                       "Low": g["low"].min(), "Close": g["close"].last()}).dropna()
    return df[df["High"] > df["Low"]]


def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def indicadores(df):
    df = df.copy()
    c, h, l = df["Close"], df["High"], df["Low"]
    df["e9"], df["e21"], df["e200"] = ema(c, 9), ema(c, 21), ema(c, 200)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["atr"] = rma(tr, 14)
    d = c.diff()
    df["rsi2"] = 100 - 100 / (1 + rma(d.clip(lower=0), 2) / rma((-d).clip(lower=0), 2))
    q80 = df["atr"].rolling(480, min_periods=200).quantile(0.8)          # ~20 días de velas de 1 h
    df["vol_ok"] = (df["atr"] <= q80) & ((h - l) <= 2 * df["atr"].shift(1))
    return df


def senales(df, rng):
    c, e9, e21, e200 = df["Close"], df["e9"], df["e21"], df["e200"]
    sube = (e9 > e21) & (e9.shift(1) <= e21.shift(1)) & (c > e200)
    baja = (e9 < e21) & (e9.shift(1) >= e21.shift(1)) & (c < e200)
    cruce = np.where(sube, 1, np.where(baja, -1, 0))
    rsi = np.where((c > e200) & (df["rsi2"] < 10), 1, np.where((c < e200) & (df["rsi2"] > 90), -1, 0))
    vol = df["vol_ok"].values
    frecuencia = np.mean(cruce != 0)
    azar = np.where(rng.random(len(df)) < frecuencia, rng.choice([-1, 1], len(df)), 0)
    cierre = df.index + pd.Timedelta(hours=1)
    validas = (np.arange(len(df)) >= 250) & (cierre.dayofweek < 5) \
        & (cierre.hour >= bot.HORA_INICIO_UTC) & (cierre.hour < bot.HORA_FIN_UTC)
    s = {0: azar, 1: cruce, 2: np.where(vol, cruce, 0), 3: rsi, 4: np.where(vol, rsi, 0)}
    return {k: np.where(validas, v, 0) for k, v in s.items()}


def simular(par, variante, df, lado, m1):
    """Recorre las señales y simula cada operación minuto a minuto."""
    T1, H1, L1, C1 = m1["T"], m1["H"], m1["L"], m1["C"]
    spr = SPREAD_PIPS.get(par, 1.5) * pip(par)
    t_ent = df.index.as_unit("ns").asi8 + HORA            # la entrada es al cierre de la vela
    close, atr = df["Close"].values, df["atr"].values
    libre, filas = -1, []
    for i in np.flatnonzero(lado):
        if t_ent[i] < libre or not atr[i] > 0:
            continue
        a = np.searchsorted(T1, t_ent[i], "left")
        b = np.searchsorted(T1, t_ent[i] + MAX_HORAS * HORA, "left")
        if b <= a:
            continue
        h, l = H1[a:b], L1[a:b]
        if lado[i] > 0:                                  # compra: entra al ask, sale al bid
            ent = close[i] + spr
            sl, tp = ent - SL_ATR * atr[i], ent + TP_ATR * atr[i]
            toca_sl, toca_tp = l <= sl, h >= tp
        else:                                            # venta: entra al bid, sale al ask
            ent = close[i]
            sl, tp = ent + SL_ATR * atr[i], ent - TP_ATR * atr[i]
            toca_sl, toca_tp = h + spr >= sl, l + spr <= tp
        k_sl = np.argmax(toca_sl) if toca_sl.any() else len(h)
        k_tp = np.argmax(toca_tp) if toca_tp.any() else len(h)
        if k_sl <= k_tp and k_sl < len(h):
            r, k, salida = -1.0, k_sl, "SL"
        elif k_tp < len(h):
            r, k, salida = TP_ATR / SL_ATR, k_tp, "TP"
        else:
            k = len(h) - 1
            fin = C1[a + k] if lado[i] > 0 else C1[a + k] + spr
            r, salida = (fin - ent) * lado[i] / (SL_ATR * atr[i]), "24h"
        libre = T1[a + k] + MIN
        filas.append((par, variante, df.index[i], r, salida))
    return filas


def caida_maxima(r):
    acum = np.cumsum(r)
    return float((acum - np.maximum.accumulate(np.r_[0, acum])[1:]).min()) if len(r) else 0.0


def resumen(x):
    r = x["R"].values
    if len(r) == 0:
        return dict(n=0)
    ganan, pierden = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), ganadas=100 * np.mean(r > 0), r_op=r.mean(), total=r.sum(),
                pf=ganan / pierden if pierden else np.inf, caida=caida_maxima(r))


def main():
    rng = np.random.default_rng(7)
    filas, inicio, fin = [], None, None
    for par in PARES:
        t0 = time.time()
        m1 = ip.velas_1m(par)
        if m1 is None or len(m1) < 100000:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        df = indicadores(a_1h(m1))
        m = dict(T=m1.index.as_unit("ns").asi8, H=m1["high"].values, L=m1["low"].values,
                 C=m1["close"].values)
        del m1
        for v, lado in senales(df, rng).items():
            filas += simular(par, v, df, lado, m)
        inicio = df.index[250] if inicio is None else min(inicio, df.index[250])
        fin = df.index[-1] if fin is None else max(fin, df.index[-1])
        print(f"{par}: {len(df)} velas de 1 h, {sum(1 for f in filas if f[0] == par)} operaciones "
              f"({time.time() - t0:.0f} s)", flush=True)
    if not filas:
        bot.enviar("⚠️ Prueba forex 1 h: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.DataFrame(filas, columns=["par", "variante", "fecha", "R", "salida"]).sort_values("fecha")
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte
    ops["tri"] = ops["fecha"].dt.tz_localize(None).dt.to_period("Q")

    tabla = []
    for (par, v), x in ops.groupby(["par", "variante"]):
        e, p = resumen(x[x["entreno"]]), resumen(x[~x["entreno"]])
        tabla.append(dict(par=par, variante=VARIANTES[v], ops_entreno=e["n"],
                          r_op_entreno=round(e.get("r_op", 0), 3), ops_prueba=p["n"],
                          r_op_prueba=round(p.get("r_op", 0), 3),
                          ganadas_prueba=round(p.get("ganadas", 0), 1)))
    pd.DataFrame(tabla).to_csv("investigacion_forex.csv", index=False)

    lineas = [
        "🔬 <b>Bot forex · velas de 1 h con SL y TP · prueba de ~3 años</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares · entradas de 7 a 20 h UTC.",
        f"SL {SL_ATR:g} ATR · TP {TP_ATR:g} ATR · cierre a las {MAX_HORAS} h · spread incluido (1 a 1,8 pips).",
        "R = lo que arriesgas por operación. Sin spread no pierdes si ganas más del 40 %.",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
    ]
    aprobadas = []
    for v, nombre in VARIANTES.items():
        x = ops[ops["variante"] == v]
        t, e, p = resumen(x), resumen(x[x["entreno"]]), resumen(x[~x["entreno"]])
        if not t["n"]:
            lineas.append(f"\n<b>{nombre}</b>: sin operaciones")
            continue
        q = x.groupby("tri")["R"].sum()
        positivos = int((q > 0).sum())
        lineas.append(
            f"\n<b>{nombre}</b>\n"
            f"• {t['n']} ops · {t['ganadas']:.1f}% ganadas · <b>{t['r_op']:+.3f} R/op</b> · total {t['total']:+.0f} R\n"
            f"• entreno {e['r_op']:+.3f} R/op ({e['n']}) · prueba {p['r_op']:+.3f} R/op ({p['n']})\n"
            f"• factor de ganancia {t['pf']:.2f} · peor caída {t['caida']:.0f} R · "
            f"trimestres positivos {positivos} de {len(q)}")
        if v and e["r_op"] > 0 and p["r_op"] > 0 and p["n"] >= MIN_OPS_PRUEBA and positivos >= len(q) / 2:
            aprobadas.append((p["r_op"], v))

    lineas.append("\n<b>Por par en la prueba</b> (R por operación)")
    for v in (1, 2, 3, 4):
        x = ops[(ops["variante"] == v) & ~ops["entreno"]]
        por_par = x.groupby("par")["R"].mean()
        lineas.append(f"{VARIANTES[v].split(')')[0]}) " +
                      " · ".join(f"{bot.nombre_par(k)} {r:+.2f}" for k, r in por_par.items()))
    if aprobadas:
        mejor = VARIANTES[max(aprobadas)[1]]
        lineas.append(f"\n✅ Pasan (positivas en entreno Y prueba, ≥{MIN_OPS_PRUEBA} ops, "
                      f"mitad de trimestres positivos): " + ", ".join(VARIANTES[v] for _, v in sorted(aprobadas, reverse=True)))
        lineas.append(f"La mejor en la prueba: <b>{mejor}</b>.")
    else:
        lineas.append("\n❌ Ninguna estrategia pasa: ninguna gana en entreno Y en prueba de forma constante.")
    lineas.append("Spreads y cierres son aproximados; un bróker real puede cobrar más. "
                  "Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
