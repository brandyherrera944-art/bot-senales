"""
Prueba honesta de estrategias (velas de 5 min, ~59 días de datos de Yahoo).

Reglas para no engañarnos:
  * Las estrategias y expiraciones están fijadas ANTES de ver los datos.
  * Los datos se dividen en ENTRENAMIENTO (primeros 2/3 de los días) y PRUEBA
    (último 1/3, días que la estrategia "no ha visto").
  * Solo se marca como CANDIDATA si supera el mínimo en los DOS tramos y con
    suficientes operaciones en la prueba.
Resultado: mensaje a Telegram + archivo investigacion.csv
"""

import math
import sys

import numpy as np
import pandas as pd

import bot

EXPIRACIONES = [1, 3, 6]          # velas de 5 min -> 5, 15 y 30 minutos
MIN_ACIERTO = 56.0                # % mínimo en entrenamiento Y en prueba
MIN_OPS_PRUEBA_PAR = 40
MIN_OPS_PRUEBA_TOTAL = 150


# ------------------------------------------------------------ indicadores extra
def extras(df):
    df = bot.indicadores(df)
    c = df["Close"]
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    p = (-d).clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    df["rsi2"] = 100 - 100 / (1 + g / p.replace(0, np.nan))
    df["ema20"] = bot.ema(c, 20)
    m = c.rolling(20).mean()
    s = c.rolling(20).std()
    df["bb_sup"], df["bb_inf"] = m + 2 * s, m - 2 * s
    df["max12"] = df["High"].shift(1).rolling(12).max()
    df["min12"] = df["Low"].shift(1).rolling(12).min()
    rango = (df["High"] - df["Low"]).replace(0, np.nan)
    df["cuerpo_rel"] = (c - df["Open"]).abs() / rango
    return df


# ------------------------------------------------------------ estrategias
# Cada una devuelve una serie con +1 (CALL), -1 (PUT) o 0 en cada vela cerrada.
def est_fibonacci(df):
    altos, bajos = bot.pivotes(df)
    lado = np.zeros(len(df))
    for i in range(bot.EMA_LENTA + 10, len(df)):
        s = bot.evaluar_vela(df, i, altos, bajos)
        if s:
            lado[i] = s["lado"]
    return pd.Series(lado, index=df.index)


def est_rsi2(df):
    """Retroceso extremo a favor de la tendencia (RSI de 2 periodos)."""
    alc = (df["Close"] > df["ema_l"]) & (df["tend_1h"] > 0)
    baj = (df["Close"] < df["ema_l"]) & (df["tend_1h"] < 0)
    return np.where(alc & (df["rsi2"] < 10), 1, np.where(baj & (df["rsi2"] > 90), -1, 0))


def est_bollinger_lateral(df):
    """Rebote en bandas de Bollinger cuando el mercado está lateral (ADX < 20)."""
    lat = df["adx"] < 20
    c, o = df["Close"], df["Open"]
    call = lat & (df["Low"] < df["bb_inf"]) & (c > df["bb_inf"]) & (c > o)
    put = lat & (df["High"] > df["bb_sup"]) & (c < df["bb_sup"]) & (c < o)
    return np.where(call, 1, np.where(put, -1, 0))


def est_ruptura(df):
    """Ruptura del máximo/mínimo de la última hora con vela fuerte y tendencia de 1 h."""
    fuerte = (df["adx"] >= 25) & (df["cuerpo_rel"] >= 0.6)
    call = fuerte & (df["tend_1h"] > 0) & (df["Close"] > df["max12"])
    put = fuerte & (df["tend_1h"] < 0) & (df["Close"] < df["min12"])
    return np.where(call, 1, np.where(put, -1, 0))


def est_ema20(df):
    """Retroceso a la EMA20 en tendencia, con vela de confirmación."""
    c, o = df["Close"], df["Open"]
    alc = (df["ema_r"] > df["ema_l"]) & (df["tend_1h"] > 0)
    baj = (df["ema_r"] < df["ema_l"]) & (df["tend_1h"] < 0)
    call = alc & (df["Low"] <= df["ema20"]) & (c > df["ema20"]) & (c > o)
    put = baj & (df["High"] >= df["ema20"]) & (c < df["ema20"]) & (c < o)
    return np.where(call, 1, np.where(put, -1, 0))


ESTRATEGIAS = {
    "Fibonacci+tendencia": est_fibonacci,
    "RSI2 extremo": est_rsi2,
    "Bollinger lateral": est_bollinger_lateral,
    "Ruptura 1h": est_ruptura,
    "Retroceso EMA20": est_ema20,
}


# ------------------------------------------------------------ evaluación
def operaciones(df, lado, exp):
    """Lista de (fecha, gano) sin operaciones solapadas y solo en horario con volumen."""
    lado = np.asarray(lado)
    c = df["Close"].values
    idx = df.index
    ops, ultimo = [], -10**9
    for i in np.flatnonzero(lado):
        if i + exp >= len(df) or i - ultimo < exp or i < bot.EMA_LENTA + 10:
            continue
        if not bot.en_horario(idx[i]):
            continue
        ultimo = i
        ops.append((idx[i], (c[i + exp] - c[i]) * lado[i] > 0))
    return ops


def pct(ops):
    return (100 * sum(g for _, g in ops) / len(ops)) if ops else 0.0


def margen(n):
    """Margen de error aproximado (95 %) en puntos porcentuales."""
    return 98 / math.sqrt(n) if n else 0


def main():
    datos = bot.descargar(bot.PARES, periodo="59d")
    filas = []
    for par, df in datos.items():
        if len(df) < 2000:
            continue
        df = extras(df)
        corte = df.index[0] + (df.index[-1] - df.index[0]) * 2 / 3
        for nombre, f in ESTRATEGIAS.items():
            lado = f(df)
            for exp in EXPIRACIONES:
                ops = operaciones(df, lado, exp)
                ent = [o for o in ops if o[0] < corte]
                pru = [o for o in ops if o[0] >= corte]
                filas.append(dict(par=par, estrategia=nombre, expiracion_min=exp * 5,
                                  ops_entreno=len(ent), acierto_entreno=round(pct(ent), 1),
                                  ops_prueba=len(pru), acierto_prueba=round(pct(pru), 1),
                                  ganadas_entreno=sum(g for _, g in ent),
                                  ganadas_prueba=sum(g for _, g in pru)))
    r = pd.DataFrame(filas)
    r.to_csv("investigacion.csv", index=False)

    # Resultado conjunto (todos los pares sumados) por estrategia y expiración
    g = r.groupby(["estrategia", "expiracion_min"]).sum(numeric_only=True).reset_index()
    g["ent"] = 100 * g["ganadas_entreno"] / g["ops_entreno"].replace(0, np.nan)
    g["pru"] = 100 * g["ganadas_prueba"] / g["ops_prueba"].replace(0, np.nan)
    g = g.sort_values("pru", ascending=False)

    lineas = ["🔬 <b>Prueba honesta de estrategias</b> (~59 días, 7 pares)",
              "Entreno = primeros 2/3 · Prueba = último 1/3 (días no vistos)",
              f"Para ganar en binarias necesitas ~54 %. Candidata = ≥{MIN_ACIERTO:.0f}% en ambos.\n",
              "<b>Todos los pares juntos:</b>"]
    for _, x in g.iterrows():
        ok = ("✅" if x["ent"] >= MIN_ACIERTO and x["pru"] >= MIN_ACIERTO
              and x["ops_prueba"] >= MIN_OPS_PRUEBA_TOTAL else "▫️")
        lineas.append(f"{ok} {x['estrategia']} {int(x['expiracion_min'])}m: "
                      f"entreno {x['ent']:.1f}% · prueba {x['pru']:.1f}% "
                      f"(±{margen(x['ops_prueba']):.0f}, {int(x['ops_prueba'])} ops)")

    cand = r[(r["acierto_entreno"] >= MIN_ACIERTO) & (r["acierto_prueba"] >= MIN_ACIERTO)
             & (r["ops_prueba"] >= MIN_OPS_PRUEBA_PAR)].sort_values("acierto_prueba", ascending=False)
    lineas.append("\n<b>Combinaciones par + estrategia que pasan:</b>")
    if cand.empty:
        lineas.append("Ninguna. 😐")
    for _, x in cand.head(10).iterrows():
        lineas.append(f"✅ {x['par']} {x['estrategia']} {x['expiracion_min']}m: "
                      f"entreno {x['acierto_entreno']}% · prueba {x['acierto_prueba']}% "
                      f"(±{margen(x['ops_prueba']):.0f}, {x['ops_prueba']} ops)")
    lineas.append(f"\nSe probaron {len(r)} combinaciones: algunas pasarán por pura suerte. "
                  "Confírmalas varias semanas en demo.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)


if __name__ == "__main__":
    sys.exit(main())
