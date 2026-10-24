"""
Prueba: entrar SOLO en las flechas de compra/venta del Lorentzian Classification, en velas de 15 min,
con expiración de 30 min (también se mide 15 y 60 min para comparar).

  - Mismo indicador y misma simulación de TradingView que investigar_lorentzian.py (configuración de
    fábrica: 8 vecinos, filtros de volatilidad y régimen, kernel). Gráfico de 5000 velas.
  - Entrada al cierre de la vela de 15 min en la que aparece la flecha, en el horario del bot.
  - Una operación abierta a la vez por par y expiración. Empates no cuentan.
  - También se muestra el "Trade Stats" como lo cuenta el indicador, para comparar.
Datos: velas de 1 min de HistData (enero 2024 a septiembre 2026) pasadas a 15 min, 7 pares del bot.
Binarias con pago 85 %: hace falta acertar más de 54,1 % para no perder (meta del bot: 56 %).
Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
"""

import sys
import time

import numpy as np
import pandas as pd

import bot
import investigar_lorentzian as lzt     # el indicador y la simulación ya probados
import investigar_rsi2 as rsi2          # descarga de HistData

EXPIRACIONES = [15, 30, 60]
VELA = 15                               # minutos por vela
META = 56.0
NS = 60 * 10**9


def velas_15m(par):
    d5 = rsi2.velas_5m(par)
    if d5 is None:
        return None
    g = d5.resample(f"{VELA}min", label="left", closed="left")
    df = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                       "Low": g["Low"].min(), "Close": g["Close"].last()}).dropna()
    return df[df["High"] > df["Low"]]


def operaciones(par, df, lado):
    c = df["Close"].values
    cierre = df.index.as_unit("ns").asi8 + VELA * NS          # la entrada es al cierre de la vela
    pos = {t: k for k, t in enumerate(cierre)}
    filas = []
    for e in EXPIRACIONES:
        libre = -1
        for i in np.flatnonzero(lado):
            if not bot.en_horario(df.index[i]):
                continue
            k = pos.get(cierre[i] + e * NS)
            if cierre[i] < libre or k is None:
                continue
            libre = cierre[i] + e * NS
            dif = (c[k] - c[i]) * lado[i]
            if dif != 0:
                filas.append((par, e, df.index[i], float(dif > 0)))
    return filas


def main():
    filas, stats, inicio, fin = [], [0, 0], None, None
    for par in lzt.PARES:
        t0 = time.time()
        df = velas_15m(par)
        if df is None or len(df) < lzt.BARRAS_GRAFICO + 2 * lzt.PASO:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        L = lzt.lorentzian(df)
        lado = np.where(L["largo"], 1, np.where(L["corto"], -1, 0))
        filas += operaciones(par, df, lado)
        stats[0] += L["stats"][0]
        stats[1] += L["stats"][1]
        desde = df.index[np.argmax(L["cubierto"])]
        inicio = desde if inicio is None else min(inicio, desde)
        fin = df.index[-1] if fin is None else max(fin, df.index[-1])
        print(f"{par}: {len(df)} velas de 15 min · {np.count_nonzero(lado)} flechas "
              f"({time.time() - t0:.0f} s)", flush=True)
    if not filas:
        bot.enviar("⚠️ Prueba Lorentzian 15 min: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.DataFrame(filas, columns=["par", "exp", "fecha", "gano"])
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte
    ops["mes"] = ops["fecha"].dt.tz_localize(None).dt.to_period("M")

    tabla = []
    for (par, e), x in ops.groupby(["par", "exp"]):
        ent, pru = x.loc[x["entreno"], "gano"], x.loc[~x["entreno"], "gano"]
        tabla.append(dict(par=par, expiracion_min=e, ops_entreno=len(ent),
                          acierto_entreno=round(100 * ent.mean(), 1) if len(ent) else None,
                          ops_prueba=len(pru), acierto_prueba=round(100 * pru.mean(), 1) if len(pru) else None))
    r = pd.DataFrame(tabla)
    r.to_csv("investigacion_lz15.csv", index=False)

    g, p = stats
    lineas = [
        "🔬 <b>Lorentzian Classification · solo sus flechas · velas de 15 min</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares · horario del bot.",
        f"Con pago 85 % no pierdes si aciertas más de {lzt.EQUILIBRIO:.1f} % (meta del bot 56 %).",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
        f"\n📈 Trade Stats como lo cuenta el indicador: <b>{100 * g / max(g + p, 1):.1f}%</b> de {g + p}",
        "\n<b>Flechas del Lorentzian</b> (todos los pares)",
    ]
    for e in EXPIRACIONES:
        x = ops[ops["exp"] == e]
        tot, _ = lzt.pct(x["gano"])
        ent, _ = lzt.pct(x.loc[x["entreno"], "gano"])
        pru, _ = lzt.pct(x.loc[~x["entreno"], "gano"])
        marca = " 👈" if e == 30 else ""
        lineas.append(f"• {e} min{marca}: {tot}\n   entreno {ent}\n   prueba {pru}")
    x = ops[ops["exp"] == 30]
    t = x.groupby("mes")["gano"].mean() * 100
    arriba = int((t > lzt.EQUILIBRIO).sum())
    lineas.append(f"\n<b>Mes por mes a 30 min</b>: {arriba} de {len(t)} meses sobre 54 %")
    lineas.append("Últimos 6: " + " · ".join(f"{lzt.nombre_mes(m)} {v:.0f}" for m, v in t.tail(6).items()))
    lineas.append("\n<b>Por par a 30 min</b> (entreno → prueba)")
    for _, f in r[r["expiracion_min"] == 30].iterrows():
        lineas.append(f"• {bot.nombre_par(f['par'])}: {f['acierto_entreno']}% ({f['ops_entreno']}) → "
                      f"{f['acierto_prueba']}% ({f['ops_prueba']})")
    cand = r[(r["acierto_entreno"] >= META) & (r["acierto_prueba"] >= META) & (r["ops_prueba"] >= 50)]
    if cand.empty:
        lineas.append("\n❌ Ningún par ni expiración supera 56 % en entreno Y en prueba.")
    else:
        lineas.append("\n✅ Superan 56 % en entreno Y en prueba: " + ", ".join(
            f"{bot.nombre_par(f['par'])} {f['expiracion_min']} min" for _, f in cand.iterrows()))
    lineas.append("Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
