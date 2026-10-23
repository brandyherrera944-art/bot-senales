"""
Indicador "Machine Learning: Lorentzian Classification" (jdehorty, v2.0, código abierto MPL-2.0)
copiado a Python para el bot. Es exactamente el mismo código que se probó en investigar_lorentzian.py.

estado_actual(df) devuelve lo que se vería en TradingView al abrir ahora un gráfico de 5000 velas:
la señal vigente (1 = compra, -1 = venta, 0 = ninguna) y hacia dónde va la línea del kernel.
"""

import math

import numpy as np
import pandas as pd

try:
    from numba import njit
except ImportError:                        # sin numba funciona igual, solo que más lento
    def njit(**_):
        return lambda f: f

BARRAS_GRAFICO = 5000                      # velas que carga el gráfico
PASO = 1000
MAX_BARS_BACK, VECINOS = 2000, 8


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


def estado_actual(df):
    """Señal vigente del Lorentzian y dirección del kernel en la última vela de df (Open/High/Low/Close)."""
    df = df.iloc[-(BARRAS_GRAFICO + 1000):]
    n = len(df)
    if n < 4000:                           # hacen falta velas viejas para buscar "vecinos"
        return None
    H = min(BARRAS_GRAFICO, n)
    Fb = caracteristicas_brutas(df)
    c = df["Close"].values
    y = np.zeros(n, np.int64)
    y[4:] = np.where(c[:-4] < c[4:], -1, np.where(c[:-4] > c[4:], 1, 0))
    filt = filtros(df)
    yhat = np.nan_to_num(kernel_rq(df["Close"]), nan=0.0)
    mp = ((df["High"] + df["Low"] + 2 * df["Open"]) / 4).values
    a = n - H
    r = lorentzian_grafico(normalizar_en_grafico(Fb[a:]), y[a:], filt[a:], yhat[a:], mp[a:], H)
    return dict(sig=int(r[0][-1]), sube=bool(yhat[-2] < yhat[-1]), baja=bool(yhat[-2] > yhat[-1]))
