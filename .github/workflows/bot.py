"""
Bot de señales Forex (velas de 5 minutos) -> Telegram
Herramienta educativa. Un % de acierto histórico NO garantiza resultados futuros.
"""

import os
import sys
import time
import logging

import numpy as np
import pandas as pd
import requests

# ------------------------------------------------------------------ CONFIG --
PARES = [p.strip().upper() for p in os.getenv(
    "PARES", "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD").split(",") if p.strip()]
INTERVALO = "5m"
MIN_VELA = 5
EXPIRACION_VELAS = int(os.getenv("EXPIRACION_VELAS", "3"))
MIN_WINRATE = float(os.getenv("MIN_WINRATE", "73"))
MIN_OPERACIONES = int(os.getenv("MIN_OPERACIONES", "30"))
DURACION_MINUTOS = int(os.getenv("DURACION_MINUTOS", "345"))
HORA_INICIO_UTC = int(os.getenv("HORA_INICIO_UTC", "7"))
HORA_FIN_UTC = int(os.getenv("HORA_FIN_UTC", "20"))
MENSAJE_INICIO = os.getenv("MENSAJE_INICIO", "1") == "1"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EMA_RAPIDA, EMA_LENTA = 50, 200
ADX_TENDENCIA, ADX_LATERAL = 22, 18
PIVOTE_IZQ, PIVOTE_DER = 5, 3
VENTANA_RANGO = 48
MAX_VELAS_DESDE_PIVOTE = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")


def enviar(texto):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("\n[MODO PRUEBA - sin Telegram]\n" + texto + "\n")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"},
            timeout=15)
        if r.status_code != 200:
            log.warning("Telegram respondió %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("No se pudo enviar a Telegram: %s", e)


def descargar(pares, periodo="5d"):
    import yfinance as yf
    tickers = " ".join(f"{p}=X" for p in pares)
    data = yf.download(tickers, interval=INTERVALO, period=periodo,
                       group_by="ticker", progress=False, auto_adjust=False, threads=True)
    salida = {}
    for p in pares:
        try:
            df = data[f"{p}=X"] if len(pares) > 1 else data
            df = df[["Open", "High", "Low", "Close"]].dropna()
            df.index = pd.to_datetime(df.index, utc=True)
            salida[p] = df
        except Exception as e:
            log.warning("Sin datos para %s: %s", p, e)
    return salida


def solo_velas_cerradas(df, ahora=None):
    ahora = ahora or pd.Timestamp.now(tz="UTC")
    cierre = df.index + pd.Timedelta(minutes=MIN_VELA)
    return df[cierre <= ahora]


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def indicadores(df):
    df = df.copy()
    h, l, c = df["High"], df["Low"], df["Close"]
    df["ema_r"] = ema(c, EMA_RAPIDA)
    df["ema_l"] = ema(c, EMA_LENTA)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["atr"] = rma(tr, 14)
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = rma(tr, 14)
    pdi = 100 * rma(pd.Series(plus_dm, index=df.index), 14) / atr
    mdi = 100 * rma(pd.Series(minus_dm, index=df.index), 14) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df["adx"] = rma(dx.fillna(0), 14)
    delta = c.diff()
    g = rma(delta.clip(lower=0), 14)
    p = rma((-delta).clip(lower=0), 14)
    df["rsi"] = 100 - 100 / (1 + g / p.replace(0, np.nan))
    return df


def pivotes(df):
    h, l = df["High"].values, df["Low"].values
    n = len(df)
    altos, bajos = [], []
    for j in range(PIVOTE_IZQ, n - PIVOTE_DER):
        if h[j] == h[j - PIVOTE_IZQ: j + PIVOTE_DER + 1].max():
            altos.append(j)
        if l[j] == l[j - PIVOTE_IZQ: j + PIVOTE_DER + 1].min():
            bajos.append(j)
    return altos, bajos


def evaluar_vela(df, i, altos, bajos):
    if i < EMA_LENTA + 10:
        return None
    o, h, l, c = (df[k].iat[i] for k in ("Open", "High", "Low", "Close"))
    er, el, adx, atr, rsi = (df[k].iat[i] for k in ("ema_r", "ema_l", "adx", "atr", "rsi"))
    rango = h - l
    if rango <= 0 or atr <= 0 or np.isnan(adx):
        return None
    vela_fuerte = abs(c - o) >= 0.5 * rango
    confirmado = i - PIVOTE_DER

    alcista = er > el and c > el and adx >= ADX_TENDENCIA
    bajista = er < el and c < el and adx >= ADX_TENDENCIA

    if alcista or bajista:
        if alcista:
            ph = [j for j in altos if j <= confirmado]
            if not ph:
                return None
            jh = ph[-1]
            pl = [j for j in bajos if j < jh]
            if not pl or i - jh > MAX_VELAS_DESDE_PIVOTE:
                return None
            jl = pl[-1]
            top, bot = df["High"].iat[jh], df["Low"].iat[jl]
            imp = top - bot
            if imp < 2 * atr:
                return None
            f382, f5, f618, f786 = (top - r * imp for r in (0.382, 0.5, 0.618, 0.786))
            minimo_desde = df["Low"].iloc[jh:i + 1].min()
            if l <= f382 and minimo_desde >= f786 and c > o and vela_fuerte and c >= f618:
                return dict(modo="Tendencia + Fibonacci", dir="CALL ⬆️ (sube)", lado=1,
                            precio=c, niveles={"0.382": f382, "0.5": f5, "0.618": f618,
                                               "Invalidación 0.786": f786})
        else:
            pl = [j for j in bajos if j <= confirmado]
            if not pl:
                return None
            jl = pl[-1]
            ph = [j for j in altos if j < jl]
            if not ph or i - jl > MAX_VELAS_DESDE_PIVOTE:
                return None
            jh = ph[-1]
            top, bot = df["High"].iat[jh], df["Low"].iat[jl]
            imp = top - bot
            if imp < 2 * atr:
                return None
            f382, f5, f618, f786 = (bot + r * imp for r in (0.382, 0.5, 0.618, 0.786))
            maximo_desde = df["High"].iloc[jl:i + 1].max()
            if h >= f382 and maximo_desde <= f786 and c < o and vela_fuerte and c <= f618:
                return dict(modo="Tendencia + Fibonacci", dir="PUT ⬇️ (baja)", lado=-1,
                            precio=c, niveles={"0.382": f382, "0.5": f5, "0.618": f618,
                                               "Invalidación 0.786": f786})
        return None

    if adx < ADX_LATERAL:
        ventana = df.iloc[i - VENTANA_RANGO:i]
        techo, piso = ventana["High"].max(), ventana["Low"].min()
        ancho = techo - piso
        if not (2 * atr <= ancho <= 8 * atr):
            return None
        zona = 0.15 * ancho
        if l <= piso + zona and c > o and c > piso and rsi <= 40 and vela_fuerte:
            return dict(modo="Lateral (rebote en piso)", dir="CALL ⬆️ (sube)", lado=1,
                        precio=c, niveles={"Piso": piso, "Techo": techo})
        if h >= techo - zona and c < o and c < techo and rsi >= 60 and vela_fuerte:
            return dict(modo="Lateral (rebote en techo)", dir="PUT ⬇️ (baja)", lado=-1,
                        precio=c, niveles={"Piso": piso, "Techo": techo})
    return None


def en_horario(ts):
    return ts.weekday() < 5 and HORA_INICIO_UTC <= ts.hour < HORA_FIN_UTC


def backtest(df):
    df = indicadores(df)
    altos, bajos = pivotes(df)
    c = df["Close"].values
    res = {"tendencia": [0, 0], "lateral": [0, 0]}
    ultimo = -10**9
    for i in range(EMA_LENTA + 10, len(df) - EXPIRACION_VELAS):
        if not en_horario(df.index[i]) or i - ultimo < EXPIRACION_VELAS:
            continue
        s = evaluar_vela(df, i, altos, bajos)
        if not s:
            continue
        ultimo = i
        k = "tendencia" if s["modo"].startswith("Tendencia") else "lateral"
        gano = (c[i + EXPIRACION_VELAS] - c[i]) * s["lado"] > 0
        res[k][0] += int(gano)
        res[k][1] += 1
    return {k: {"ganadas": g, "total": t, "winrate": (100 * g / t if t else 0.0)}
            for k, (g, t) in res.items()}


def fmt(p, x):
    return f"{x:.3f}" if p.endswith("JPY") else f"{x:.5f}"


def mensaje_senal(par, s, stats, vela_ts):
    hora_col = (vela_ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
    niveles = "\n".join(f"  • {k}: {fmt(par, v)}" for k, v in s["niveles"].items())
    return (
        f"📊 <b>SEÑAL {par}</b> — {s['dir']}\n"
        f"Estrategia: {s['modo']}\n"
        f"Precio de referencia: {fmt(par, s['precio'])}\n"
        f"Vela de 5 min cerrada: {hora_col:%H:%M} (hora Colombia)\n"
        f"Expiración sugerida: {EXPIRACION_VELAS * MIN_VELA} min\n\n"
        f"Niveles:\n{niveles}\n\n"
        f"Acierto histórico de esta estrategia en {par}: "
        f"<b>{stats['winrate']:.1f}%</b> ({stats['ganadas']}/{stats['total']}, últimos ~60 días)\n"
        f"⚠️ Educativo. El % histórico no garantiza el resultado."
    )


def mensaje_ranking(tabla):
    filas = []
    for par, st in tabla.items():
        for k in ("tendencia", "lateral"):
            d = st[k]
            ok = "✅" if d["total"] >= MIN_OPERACIONES and d["winrate"] >= MIN_WINRATE else "▫️"
            filas.append((d["winrate"], f"{ok} {par} {k}: {d['winrate']:.1f}% ({d['total']} ops)"))
    filas.sort(reverse=True)
    lineas = "\n".join(f for _, f in filas)
    return (f"🤖 <b>Bot de señales activo</b> (5 min, expiración {EXPIRACION_VELAS * MIN_VELA} min)\n"
            f"Solo envío señales con ✅ (≥{MIN_WINRATE:.0f}% y ≥{MIN_OPERACIONES} operaciones):\n\n"
            f"{lineas}")


def calcular_tabla():
    datos = descargar(PARES, periodo="60d")
    return {p: backtest(df) for p, df in datos.items() if len(df) > EMA_LENTA + 50}


def aprobado(st):
    return st["total"] >= MIN_OPERACIONES and st["winrate"] >= MIN_WINRATE


def main():
    fin = time.time() + DURACION_MINUTOS * 60
    tabla = calcular_tabla()
    ultima_tabla = time.time()
    if MENSAJE_INICIO:
        enviar(mensaje_ranking(tabla))

    enviadas = {}
    ya_vistas = set()

    while time.time() < fin:
        ahora = time.time()
        siguiente = (ahora // (MIN_VELA * 60) + 1) * MIN_VELA * 60 + 25
        time.sleep(max(1, siguiente - ahora))

        if time.time() - ultima_tabla > 3600:
            try:
                tabla = calcular_tabla()
                ultima_tabla = time.time()
            except Exception as e:
                log.warning("Error recalculando backtest: %s", e)

        try:
            datos = descargar(PARES, periodo="5d")
        except Exception as e:
            log.warning("Error descargando datos: %s", e)
            continue

        for par, df in datos.items():
            df = solo_velas_cerradas(df)
            if len(df) < EMA_LENTA + 50:
                continue
            df = indicadores(df)
            i = len(df) - 1
            ts = df.index[i]

            for clave, s in list(enviadas.items()):
                if clave[0] != par or clave[1] not in df.index:
                    continue
                k = df.index.get_loc(clave[1]) + EXPIRACION_VELAS
                if k <= i:
                    cierre = df["Close"].iat[k]
                    gano = (cierre - s["precio"]) * s["lado"] > 0
                    enviar(f"{'✅ GANADA' if gano else '❌ PERDIDA'} — {par} {s['dir']}\n"
                           f"Entrada {fmt(par, s['precio'])} → cierre {fmt(par, cierre)}")
                    del enviadas[clave]

            if (par, ts) in ya_vistas or not en_horario(ts):
                continue
            ya_vistas.add((par, ts))
            if any(c[0] == par for c in enviadas):
                continue

            altos, bajos = pivotes(df)
            s = evaluar_vela(df, i, altos, bajos)
            if not s or par not in tabla:
                continue
            k = "tendencia" if s["modo"].startswith("Tendencia") else "lateral"
            if aprobado(tabla[par][k]):
                enviar(mensaje_senal(par, s, tabla[par][k], ts))
                enviadas[(par, ts)] = s
            else:
                log.info("Señal %s %s descartada (acierto %.1f%%)", par, s["modo"],
                         tabla[par][k]["winrate"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        enviar(mensaje_ranking(calcular_tabla()))
    else:
        main()
