"""
Prueba: ¿mejora la VIGILANCIA RSI2 si se combina con el indicador de TradingView
"Machine Learning: Lorentzian Classification" (jdehorty, v2.0, código abierto MPL-2.0)?

Variantes (velas de 5 min, horario del bot, una operación abierta a la vez por par y expiración):
  A) RSI2 sola: la misma regla de bot.py (EMA200 de 5 min + tendencia de 1 h + RSI(2) < 10 / > 90).
  B) Lorentzian solo: las flechas de compra/venta del indicador (configuración de fábrica).
  C) RSI2 + Lorentzian: señal RSI2 solo si la señal vigente del Lorentzian va al mismo lado.
  D) C + kernel a favor: además la línea del kernel del indicador sube (CALL) o baja (PUT).
Expiraciones 15, 30 y 60 min. Empates no cuentan. Entreno = primeros 2/3 · Prueba = último 1/3.

Cómo se copia el Lorentzian (para que sea lo que se ve en TradingView):
  - Mismas 5 características (RSI 14, WT 10/11, CCI 20, ADX 20, RSI 9), mismo vecino más cercano
    con distancia de Lorentz, 8 vecinos, filtros de volatilidad y de régimen, kernel 8/8/25.
  - TradingView solo calcula las últimas 2000 velas del gráfico y busca "vecinos" en velas más
    viejas del mismo gráfico. Aquí se simula un gráfico de 5000 velas que se vuelve a cargar cada
    1000 velas, y solo se usan las señales de sus últimas 1000 velas. Nunca se usa el futuro.
  - Además se calcula el "Trade Stats" como lo cuenta el propio indicador, para comparar.

Datos: velas de 1 min de HistData (enero 2024 a septiembre 2026) pasadas a 5 min, 7 pares del bot.
Binarias con pago 85 %: hace falta acertar más de 54,1 % para no perder (meta del bot: 56 %).
"""

import math
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

import bot
import investigar_rsi2 as rsi2

PARES = [p.strip().upper() for p in os.getenv("PARES_LZ", ",".join(bot.PARES)).split(",") if p.strip()]
EXPIRACIONES = [15, 30, 60]
PAGO = 0.85
EQUILIBRIO = 100 / (1 + PAGO)          # 54,05 %
META = 56.0
MIN_OPS_PRUEBA_PAR = 50
BARRAS_GRAFICO = int(os.getenv("BARRAS_GRAFICO") or 5000)   # velas que carga el gráfico
PASO = 1000                                                # cada cuántas velas se "recarga"
MAX_BARS_BACK, VECINOS = 2000, 8
VARIANTES = {"A": "A) RSI2 sola", "B": "B) Lorentzian solo",
             "C": "C) RSI2 + Lorentzian", "D": "D) RSI2 + Lorentzian + kernel"}


# ---------------------------------------------------- INDICADORES (PINE) ---
def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    return 100 - 100 / (1 + rma(d.clip(lower=0), n) / rma((-d).clip(lower=0), n))


def cci(c, n):
    m = c.rolling(n).mean()
    dev = c.rolling(n).apply(lambda v: np.mean(np.abs(v - v.mean())), raw=True)
    return (c - m) / (0.015 * dev)


def wt_bruto(src, n1, n2):
    e1 = ema(src, n1)
    e2 = ema((src - e1).abs(), n1)
    ci = (src - e1) / (0.015 * e2)
    wt1 = ema(ci, n2)
    return wt1 - wt1.rolling(4).mean()


def adx_ml(h, l, c, n):
    """n_adx de MLExtensions (suavizado de Wilder sin dividir), reescalado a 0-1."""
    pc, ph, pl = c.shift(1).fillna(0), h.shift(1).fillna(0), l.shift(1).fillna(0)
    tr = np.maximum(np.maximum(h - l, (h - pc).abs()), (l - pc).abs()).values
    up, dn = (h - ph).values, (pl - l).values
    dmp = np.where(up > dn, np.maximum(up, 0), 0.0)
    dmn = np.where(dn > up, np.maximum(dn, 0), 0.0)
    sm = np.zeros((3, len(tr)))
    acc = np.zeros(3)
    for k, (a, b, d) in enumerate(zip(tr, dmp, dmn)):
        acc = acc - acc / n + np.array([a, b, d])
        sm[:, k] = acc
    dip, din = 100 * sm[1] / sm[0], 100 * sm[2] / sm[0]
    dx = pd.Series(100 * np.abs(dip - din) / (dip + din), index=c.index)
    return rma(dx, n) / 100


def kernel_rq(c, h=8, r=8.0, x=25):
    """kernels.rationalQuadratic: promedio ponderado de los últimos x+2 cierres."""
    i = np.arange(0, x + 2)
    w = (1 + i ** 2 / (h ** 2 * 2 * r)) ** (-r)
    v = c.values
    out = np.full(len(v), np.nan)
    for k in range(x + 1, len(v)):
        out[k] = np.dot(w, v[k - i]) / w.sum()
    return out


def filtros(df):
    """Filtro de volatilidad (ATR 1 > ATR 10) y de régimen (umbral -0,1), por defecto activos."""
    h, l, c, o = df["High"], df["Low"], df["Close"], df["Open"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    volat = (tr > rma(tr, 10)).values
    src = ((o + h + l + c) / 4).values
    hl = (h - l).values
    v1 = v2 = k_ant = 0.0
    pend = np.zeros(len(src))
    for t in range(len(src)):
        v1 = 0.2 * (src[t] - src[t - 1]) + 0.8 * v1 if t else 0.0
        v2 = 0.1 * hl[t] + 0.8 * v2
        om = abs(v1 / v2) if v2 else 0.0
        al = (-om ** 2 + math.sqrt(om ** 4 + 16 * om ** 2)) / 8
        k = al * src[t] + (1 - al) * k_ant
        pend[t] = abs(k - k_ant)
        k_ant = k
    p = pd.Series(pend)
    e = ema(p, 200)
    regimen = ((p - e) / e >= -0.1).values
    return volat & regimen


def caracteristicas_brutas(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    hlc3 = (h + l + c) / 3
    return np.column_stack([
        (rsi(c, 14) / 100).values,          # n_rsi(close, 14, 1)
        wt_bruto(hlc3, 10, 11).values,      # n_wt(hlc3, 10, 11) antes de normalizar
        cci(c, 20).values,                  # n_cci(close, 20, 1) antes de normalizar
        adx_ml(h, l, c, 20).values,         # n_adx(20)
        (rsi(c, 9) / 100).values,           # n_rsi(close, 9, 1)
    ])


def normalizar_en_grafico(F):
    """normalize() de MLExtensions: mínimo y máximo desde la primera vela del gráfico."""
    G = F.copy()
    for k in (1, 2):
        s = pd.Series(F[:, k])
        mn, mx = s.cummin().values, s.cummax().values
        G[:, k] = (F[:, k] - mn) / np.maximum(mx - mn, 1e-9)
    return np.nan_to_num(G, nan=0.0)


# ------------------------------------------------- LORENTZIAN (UN GRÁFICO) --
@njit(cache=True)
def lorentzian_grafico(F, y, filt, yhat, mp, H):
    mbi = H - 1 - MAX_BARS_BACK if H - 1 >= MAX_BARS_BACK else 0
    sig = np.zeros(H, np.int64)
    cambio = np.zeros(H, np.bool_)
    sL = np.zeros(H, np.bool_)
    sS = np.zeros(H, np.bool_)
    ganada = np.zeros(H, np.int64)
    perdida = np.zeros(H, np.int64)
    dist = np.zeros(VECINOS + 2)
    pred = np.zeros(VECINOS + 2, np.int64)
    n = 0
    prediccion = 0
    held = 0
    ent_l = mp[0]
    ent_s = mp[0]
    for b in range(1, H):
        if b >= mbi:
            last = -1.0
            fin = min(MAX_BARS_BACK - 1, b)
            paso = -1 if mbi > fin else 1
            i = mbi
            while True:
                if i % 4 != 0:
                    d = 0.0
                    for k in range(F.shape[1]):
                        d += math.log(1 + abs(F[b, k] - F[i, k]))
                    if d >= last:
                        last = d
                        dist[n] = d
                        pred[n] = y[i]
                        n += 1
                        if n > VECINOS:
                            last = dist[6]                   # round(8 * 3 / 4)
                            for j in range(n - 1):
                                dist[j] = dist[j + 1]
                                pred[j] = pred[j + 1]
                            n -= 1
                if i == fin:
                    break
                i += paso
            prediccion = 0
            for j in range(n):
                prediccion += pred[j]
        s = sig[b - 1]
        if prediccion > 0 and filt[b]:
            s = 1
        elif prediccion < 0 and filt[b]:
            s = -1
        sig[b] = s
        cambio[b] = s != sig[b - 1]
        held = 0 if cambio[b] else held + 1
        sube = yhat[b - 1] < yhat[b]
        baja = yhat[b - 1] > yhat[b]
        sL[b] = s == 1 and cambio[b] and sube
        sS[b] = s == -1 and cambio[b] and baja
        # ---- "Trade Stats" como lo cuenta el indicador (salida estricta a las 4 velas)
        if b >= 4:
            cuatro, menos = held == 4, 0 < held < 4
            nueva_venta, nueva_compra = s == -1 and cambio[b], s == 1 and cambio[b]
            fin_l = ((cuatro and sig[b - 4] == 1) or (menos and nueva_venta and sig[b - 4] == 1)) and sL[b - 4]
            fin_s = ((cuatro and sig[b - 4] == -1) or (menos and nueva_compra and sig[b - 4] == -1)) and sS[b - 4]
            if b > mbi:
                if sL[b]:
                    ent_s = 0.0
                    ent_l = mp[b]
                if fin_l:
                    dl = mp[b] - ent_l
                    if dl > 0:
                        ganada[b] += 1
                    elif dl < 0:
                        perdida[b] += 1
                if sS[b]:
                    ent_l = 0.0
                    ent_s = mp[b]
                if fin_s:
                    ds = ent_s - mp[b]
                    if ds > 0:
                        ganada[b] += 1
                    elif ds < 0:
                        perdida[b] += 1
    return sig, sL, sS, ganada, perdida


def lorentzian(df):
    """Simula el indicador como se ve en TradingView, gráfico tras gráfico, sin mirar el futuro."""
    n = len(df)
    Fb = caracteristicas_brutas(df)
    c = df["Close"].values
    y = np.zeros(n, np.int64)
    y[4:] = np.where(c[:-4] < c[4:], -1, np.where(c[:-4] > c[4:], 1, 0))   # src[4] vs src[0]
    filt = filtros(df)
    yhat = np.nan_to_num(kernel_rq(df["Close"]), nan=0.0)
    mp = ((df["High"] + df["Low"] + 2 * df["Open"]) / 4).values
    sig = np.zeros(n, np.int64)
    sL = np.zeros(n, bool)
    sS = np.zeros(n, bool)
    sube = np.zeros(n, bool)
    sube[1:] = yhat[:-1] < yhat[1:]
    baja = np.zeros(n, bool)
    baja[1:] = yhat[:-1] > yhat[1:]
    gan = per = 0
    cubierto = np.zeros(n, bool)
    H = BARRAS_GRAFICO
    for fin in range(H - 1, n, PASO):
        a = fin - H + 1
        r = lorentzian_grafico(normalizar_en_grafico(Fb[a:fin + 1]), y[a:fin + 1],
                               filt[a:fin + 1], yhat[a:fin + 1], mp[a:fin + 1], H)
        usa = slice(H - PASO, H)
        g = slice(fin - PASO + 1, fin + 1)
        sig[g], sL[g], sS[g] = r[0][usa], r[1][usa], r[2][usa]
        gan += int(r[3][usa].sum())
        per += int(r[4][usa].sum())
        cubierto[g] = True
    return dict(sig=sig, largo=sL, corto=sS, sube=sube, baja=baja, cubierto=cubierto,
                stats=(gan, per))


# ---------------------------------------------------------- OPERACIONES ----
def operaciones(par, variante, df, lado):
    c = df["Close"].values
    t5 = df.index.as_unit("ns").asi8
    cierre = t5 + 5 * 60 * 10**9                     # la entrada es al cierre de la vela
    pos = {t: k for k, t in enumerate(cierre)}
    filas = []
    for e in EXPIRACIONES:
        libre = -1
        for i in np.flatnonzero(lado):
            if i < bot.EMA_LENTA + 10 or not bot.en_horario(df.index[i]):
                continue
            ent = cierre[i]
            k = pos.get(ent + e * 60 * 10**9)
            if ent < libre or k is None:
                continue
            libre = ent + e * 60 * 10**9
            dif = (c[k] - c[i]) * lado[i]
            if dif != 0:
                filas.append((par, variante, e, df.index[i], float(dif > 0)))
    return filas


def pct(x):
    x = x.dropna()
    if len(x) == 0:
        return "sin ops", None
    p = 100 * x.mean()
    margen = 196 * np.sqrt(x.mean() * (1 - x.mean()) / len(x))
    ganancia = len(x) * (x.mean() * PAGO - (1 - x.mean()))
    return f"{p:.1f}% de {len(x)} (±{margen:.1f}) · {ganancia:+.0f}", p


def main():
    filas, stats, inicio, fin = [], [0, 0], None, None
    for par in PARES:
        df = rsi2.velas_5m(par)
        if df is None or len(df) < BARRAS_GRAFICO + 2 * PASO:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        t0 = time.time()
        L = lorentzian(df)
        ind = bot.indicadores(df)
        c = ind["Close"]
        r2 = np.where((c > ind["ema_l"]) & (ind["tend_1h"] > 0) & (ind["rsi2"] < 10), 1,
                      np.where((c < ind["ema_l"]) & (ind["tend_1h"] < 0) & (ind["rsi2"] > 90), -1, 0))
        r2 = np.where(L["cubierto"], r2, 0)            # mismo periodo para todas las variantes
        lz = np.where(L["largo"], 1, np.where(L["corto"], -1, 0))
        comb = np.where((r2 != 0) & (L["sig"] == r2), r2, 0)
        kern = np.where(((comb > 0) & L["sube"]) | ((comb < 0) & L["baja"]), comb, 0)
        for v, lado in (("A", r2), ("B", lz), ("C", comb), ("D", kern)):
            filas += operaciones(par, v, df, lado)
        stats[0] += L["stats"][0]
        stats[1] += L["stats"][1]
        desde = df.index[np.argmax(L["cubierto"])]
        inicio = desde if inicio is None else min(inicio, desde)
        fin = df.index[-1] if fin is None else max(fin, df.index[-1])
        print(f"{par}: {len(df)} velas · señales RSI2 {np.count_nonzero(r2)} · Lorentzian "
              f"{np.count_nonzero(lz)} · combinadas {np.count_nonzero(comb)} / {np.count_nonzero(kern)} "
              f"({time.time() - t0:.0f} s)", flush=True)
    if not filas:
        bot.enviar("⚠️ Prueba RSI2 + Lorentzian: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.DataFrame(filas, columns=["par", "variante", "exp", "fecha", "gano"])
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte

    resumen = []
    for (par, v, e), x in ops.groupby(["par", "variante", "exp"]):
        ent, pru = x.loc[x["entreno"], "gano"], x.loc[~x["entreno"], "gano"]
        resumen.append(dict(par=par, variante=VARIANTES[v], expiracion_min=e,
                            ops_entreno=len(ent), acierto_entreno=round(100 * ent.mean(), 1) if len(ent) else None,
                            ops_prueba=len(pru), acierto_prueba=round(100 * pru.mean(), 1) if len(pru) else None))
    r = pd.DataFrame(resumen)
    r.to_csv("investigacion_lorentzian.csv", index=False)

    g, p = stats
    lineas = [
        "🔬 <b>RSI2 + Lorentzian Classification · velas de 5 min</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares · horario del bot.",
        f"Con pago 85 % no pierdes si aciertas más de {EQUILIBRIO:.1f} % (meta del bot 56 %).",
        "Formato: acierto de N ops (± margen) · ganancia en montos de 1 operación.",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
        f"\n📈 Trade Stats como lo cuenta el indicador: <b>{100 * g / max(g + p, 1):.1f}%</b> "
        f"de {g + p} (entra y sale con el promedio de la vela, que incluye su máximo y mínimo).",
    ]
    for v, nombre in VARIANTES.items():
        lineas.append(f"\n<b>{nombre}</b> (todos los pares)")
        for e in EXPIRACIONES:
            x = ops[(ops["variante"] == v) & (ops["exp"] == e)]
            tot, _ = pct(x["gano"])
            ent, _ = pct(x.loc[x["entreno"], "gano"])
            pru, _ = pct(x.loc[~x["entreno"], "gano"])
            lineas.append(f"• {e} min: {tot}\n   entreno {ent}\n   prueba {pru}")

    cand = r[(r["acierto_entreno"] >= META) & (r["acierto_prueba"] >= META)
             & (r["ops_prueba"] >= MIN_OPS_PRUEBA_PAR)].sort_values("acierto_prueba", ascending=False)
    lineas.append(f"\n<b>Par + variante que superan {META:.0f} % en entreno Y en prueba</b> "
                  f"(≥{MIN_OPS_PRUEBA_PAR} ops en prueba):")
    if cand.empty:
        lineas.append("❌ Ninguna.")
    for _, x in cand.head(8).iterrows():
        lineas.append(f"✅ {bot.nombre_par(x['par'])} {x['variante']} · {x['expiracion_min']} min: "
                      f"entreno {x['acierto_entreno']}% ({x['ops_entreno']}) · "
                      f"prueba {x['acierto_prueba']}% ({x['ops_prueba']})")
    lineas.append(f"\nSe probaron {len(r)} combinaciones: algunas pueden pasar por pura suerte.")
    lineas.append("Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
