"""
Prueba honesta: CAMBIO DE TENDENCIA con VELA ENVOLVENTE + VELA DE CONFIRMACIÓN (velas de 1 min).

Patrón alcista (CALL):
  1) vela bajista, 2) vela alcista ENVOLVENTE (su cuerpo cubre el cuerpo anterior),
  3) vela de CONFIRMACIÓN alcista que cierra por encima del máximo de la envolvente.
  La señal se da al cierre de la vela de confirmación. (Bajista/PUT: al revés.)

Variantes fijadas ANTES de ver datos:
  A) Envolvente + confirmación
  B) A + venía una tendencia contraria (los últimos 10 min iban en contra y bajo/sobre la EMA20)
  C) B + la envolvente marca el extremo de los últimos 15 min (giro en un mínimo/máximo)
Expiraciones: 1, 2, 3 y 5 minutos. Empates no cuentan (en binarias se devuelve el dinero).
Datos: ~28 días de velas de 1 min (límite de Yahoo). Entreno = primeros 2/3, prueba = último 1/3.
"""

import math
import sys

import numpy as np
import pandas as pd

import bot

EXPIRACIONES = [1, 2, 3, 5]
MIN_ACIERTO = 56.0
MIN_OPS_PRUEBA_PAR = 40
MIN_OPS_PRUEBA_TOTAL = 150


def descargar_1m(pares):
    """Yahoo solo deja 7 días de velas de 1 min por pedido y hasta 30 días atrás."""
    import yfinance as yf
    tickers = " ".join(f"{p}=X" for p in pares)
    fin = pd.Timestamp.now(tz="UTC").floor("min")
    partes = {p: [] for p in pares}
    for k in range(4):
        hasta = fin - pd.Timedelta(days=7 * k)
        desde = hasta - pd.Timedelta(days=7)
        try:
            data = yf.download(tickers, interval="1m", start=desde.to_pydatetime(),
                               end=hasta.to_pydatetime(), group_by="ticker",
                               progress=False, auto_adjust=False, threads=True)
        except Exception as e:  # noqa: BLE001
            print("Error descargando tramo", k, e)
            continue
        for p in pares:
            try:
                df = data[f"{p}=X"] if len(pares) > 1 else data
                df = df[["Open", "High", "Low", "Close"]].dropna()
                df.index = pd.to_datetime(df.index, utc=True)
                partes[p].append(df)
            except Exception as e:  # noqa: BLE001
                print("Sin datos", p, k, e)
    salida = {}
    for p, lst in partes.items():
        if lst:
            df = pd.concat(lst).sort_index()
            salida[p] = df[~df.index.duplicated(keep="last")]
    return salida


def senales(df):
    """Devuelve {variante: array con +1/-1/0} marcado en la vela de CONFIRMACIÓN."""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(df)
    ema20 = bot.ema(df["Close"], 20).values
    A = np.zeros(n)
    B = np.zeros(n)
    C = np.zeros(n)
    for k in range(17, n):
        e, p = k - 1, k - 2          # e = envolvente, p = vela anterior, k = confirmación
        cuerpo_e, cuerpo_p = abs(c[e] - o[e]), abs(c[p] - o[p])
        if cuerpo_p == 0 or cuerpo_e <= cuerpo_p:
            continue
        alc = (c[p] < o[p] and c[e] > o[e] and o[e] <= c[p] and c[e] >= o[p]
               and c[k] > o[k] and c[k] > h[e])
        baj = (c[p] > o[p] and c[e] < o[e] and o[e] >= c[p] and c[e] <= o[p]
               and c[k] < o[k] and c[k] < l[e])
        if not (alc or baj):
            continue
        lado = 1 if alc else -1
        A[k] = lado
        venia_contra = (c[p] < c[p - 10] and c[p] < ema20[p]) if alc else \
                       (c[p] > c[p - 10] and c[p] > ema20[p])
        if venia_contra:
            B[k] = lado
            extremo = (min(l[p], l[e]) <= l[e - 15:e + 1].min()) if alc else \
                      (max(h[p], h[e]) >= h[e - 15:e + 1].max())
            if extremo:
                C[k] = lado
    return {"A) Envolvente+confirmación": A,
            "B) A tras tendencia contraria": B,
            "C) B en mínimo/máximo de 15 min": C}


def operaciones(df, lado, exp):
    c = df["Close"].values
    idx = df.index
    ops, ultimo = [], -10**9
    for i in np.flatnonzero(lado):
        if i + exp >= len(df) or i - ultimo < exp or not bot.en_horario(idx[i]):
            continue
        # la vela de expiración debe estar exactamente exp minutos después (sin huecos de datos)
        if idx[i + exp] - idx[i] != pd.Timedelta(minutes=exp):
            continue
        ultimo = i
        dif = (c[i + exp] - c[i]) * lado[i]
        if dif == 0:
            continue
        ops.append((idx[i], dif > 0))
    return ops


def pct(ops):
    return (100 * sum(g for _, g in ops) / len(ops)) if ops else 0.0


def margen(n):
    return 98 / math.sqrt(n) if n else 0


def main():
    datos = descargar_1m(bot.PARES)
    filas, dias = [], 0
    for par, df in datos.items():
        if len(df) < 3000:
            continue
        dias = max(dias, (df.index[-1] - df.index[0]).days)
        corte = df.index[0] + (df.index[-1] - df.index[0]) * 2 / 3
        for nombre, lado in senales(df).items():
            for exp in EXPIRACIONES:
                ops = operaciones(df, lado, exp)
                ent = [x for x in ops if x[0] < corte]
                pru = [x for x in ops if x[0] >= corte]
                filas.append(dict(par=par, estrategia=nombre, expiracion_min=exp,
                                  ops_entreno=len(ent), acierto_entreno=round(pct(ent), 1),
                                  ops_prueba=len(pru), acierto_prueba=round(pct(pru), 1),
                                  ganadas_entreno=sum(g for _, g in ent),
                                  ganadas_prueba=sum(g for _, g in pru)))
    r = pd.DataFrame(filas)
    r.to_csv("investigacion_1m.csv", index=False)
    if r.empty:
        bot.enviar("🔬 Prueba de envolventes 1 min: no se pudieron descargar datos de 1 minuto.")
        return

    g = r.groupby(["estrategia", "expiracion_min"]).sum(numeric_only=True).reset_index()
    g["ent"] = 100 * g["ganadas_entreno"] / g["ops_entreno"].replace(0, np.nan)
    g["pru"] = 100 * g["ganadas_prueba"] / g["ops_prueba"].replace(0, np.nan)
    g = g.sort_values("pru", ascending=False)

    lineas = [f"🔬 <b>Prueba: envolvente + confirmación (velas 1 min)</b> (~{dias} días, 7 pares)",
              "Entreno = primeros 2/3 · Prueba = último 1/3 (días no vistos)",
              f"Para ganar en binarias necesitas ~54 %. Candidata = ≥{MIN_ACIERTO:.0f}% en ambos.\n",
              "<b>Todos los pares juntos:</b>"]
    for _, x in g.iterrows():
        ok = ("✅" if x["ent"] >= MIN_ACIERTO and x["pru"] >= MIN_ACIERTO
              and x["ops_prueba"] >= MIN_OPS_PRUEBA_TOTAL else "▫️")
        lineas.append(f"{ok} {x['estrategia']} · {int(x['expiracion_min'])} min: "
                      f"entreno {x['ent']:.1f}% · prueba {x['pru']:.1f}% "
                      f"(±{margen(x['ops_prueba']):.0f}, {int(x['ops_prueba'])} ops)")

    cand = r[(r["acierto_entreno"] >= MIN_ACIERTO) & (r["acierto_prueba"] >= MIN_ACIERTO)
             & (r["ops_prueba"] >= MIN_OPS_PRUEBA_PAR)].sort_values("acierto_prueba", ascending=False)
    lineas.append("\n<b>Par + variante que pasan:</b>")
    if cand.empty:
        lineas.append("Ninguna. 😐")
    for _, x in cand.head(10).iterrows():
        lineas.append(f"✅ {x['par']} {x['estrategia']} · {x['expiracion_min']} min: "
                      f"entreno {x['acierto_entreno']}% · prueba {x['acierto_prueba']}% "
                      f"(±{margen(x['ops_prueba']):.0f}, {x['ops_prueba']} ops)")
    lineas.append(f"\nSe probaron {len(r)} combinaciones: algunas pasarán por pura suerte.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)


if __name__ == "__main__":
    sys.exit(main())
