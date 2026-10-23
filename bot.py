"""
Bot de señales Forex (velas de 5 min) -> Telegram. Tendencia + Fibonacci, lateral,
filtro de 1 hora, resumen diario. Herramienta educativa: el % histórico NO garantiza resultados.
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
MIN_VELA = 5                                                   # minutos por vela
EXPIRACION_VELAS = int(os.getenv("EXPIRACION_VELAS", "3"))     # 3 velas = 15 min
MIN_WINRATE = float(os.getenv("MIN_WINRATE", "73"))
MIN_OPERACIONES = int(os.getenv("MIN_OPERACIONES", "30"))
DURACION_MINUTOS = int(os.getenv("DURACION_MINUTOS", "345"))   # cuánto corre cada ejecución
HORA_INICIO_UTC = int(os.getenv("HORA_INICIO_UTC", "7"))       # 2 a. m. Colombia
HORA_FIN_UTC = int(os.getenv("HORA_FIN_UTC", "20"))            # 3 p. m. Colombia
MENSAJE_INICIO = os.getenv("MENSAJE_INICIO", "1") == "1"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip().removeprefix("bot")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ARCHIVO_RESULTADOS = os.getenv("ARCHIVO_RESULTADOS", "resultados.csv")
FILTRO_1H = os.getenv("FILTRO_1H", "1") == "1"
EMA_1H = 50

# Modo vigilancia: estrategia RSI2 en pares/expiraciones candidatas.
# Solo OBSERVA y mide resultados reales; no es una recomendación para operar.
VIGILANCIA = {}
for _x in os.getenv("VIGILANCIA", "USDCAD:3,USDCAD:6,GBPUSD:6").split(","):
    if ":" in _x:
        _p, _e = _x.strip().upper().split(":")
        VIGILANCIA.setdefault(_p, []).append(int(_e))
ARCHIVO_VIGILANCIA = "vigilancia.csv"

# Vigilancia "D": RSI2 + Lorentzian Classification + kernel (15 min, todos los pares).
# Solo OBSERVA: en la prueba de 3 años dio 50,6 % (ago-sep 2026: 56-58 %), no está aprobada.
VIG_LZ = os.getenv("VIGILANCIA_LZ", "1") == "1"
LZ_EXP_VELAS = 3                       # 3 velas de 5 min = 15 min
ARCHIVO_VIG_LZ = "vigilancia_lz.csv"
try:
    import lorentzian as lz
except Exception as _e:  # noqa: BLE001
    lz = None
    logging.getLogger("bot").warning("Sin Lorentzian (%s): la vigilancia D queda apagada.", _e)

# Parámetros de la estrategia
EMA_RAPIDA, EMA_LENTA = 50, 200
ADX_TENDENCIA, ADX_LATERAL = 22, 18
PIVOTE_IZQ, PIVOTE_DER = 5, 3
VENTANA_RANGO = 48            # 48 velas de 5 min = 4 horas
MAX_VELAS_DESDE_PIVOTE = 30

sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")


# ------------------------------------------------------------- TELEGRAM ----
def enviar(texto: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("\n[MODO PRUEBA - sin Telegram]\n" + texto + "\n")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"},
            timeout=15)
        if r.status_code == 404:
            log.warning("Telegram 404: el TELEGRAM_TOKEN no es válido. Revisa el secreto en GitHub.")
        elif r.status_code == 400 and "chat not found" in r.text:
            log.warning("Telegram 400: TELEGRAM_CHAT_ID incorrecto o no le escribiste 'hola' a tu bot.")
        elif r.status_code != 200:
            log.warning("Telegram respondió %s: %s", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo enviar a Telegram: %s", e)


def enviar_foto(png: bytes, texto: str) -> bool:
    """Manda una imagen con su texto. Devuelve False si no se pudo."""
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("\n[MODO PRUEBA - sin Telegram, con gráfico]\n" + texto + "\n")
        return True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": texto, "parse_mode": "HTML"},
            files={"photo": ("grafico.png", png, "image/png")},
            timeout=30)
        if r.status_code == 200:
            return True
        log.warning("Telegram (foto) respondió %s: %s", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo enviar la foto a Telegram: %s", e)
    return False


def enviar_con_grafico(texto: str, df, **datos) -> None:
    """Manda el mensaje con el gráfico de la operación. Si algo falla, manda solo el texto."""
    png = None
    if len(texto) <= 1024:                       # límite de Telegram para el texto de una foto
        try:
            png = grafico_vig(df, **datos)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo dibujar el gráfico: %s", e)
    if png is None or not enviar_foto(png, texto):
        enviar(texto)


# ------------------------------------------------------------ GRÁFICOS -----
def grafico_vig(df, par, lado, ts, precio, minutos=None, k=None, cierre=None, res=None,
                estrategia="RSI2"):
    """Gráfico de una operación de vigilancia RSI2 (velas de 5 min).
    Al abrir: velas, EMA200, RSI(2) y la entrada. Al cerrar: también el cierre y el resultado."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    verde, rojo, azul, gris = "#26a69a", "#ef5350", "#1e63d6", "#6b7280"
    ie = df.index.get_loc(ts)
    fin = min(len(df), (k if k is not None else ie) + 3)
    ini = max(0, ie - 40)
    v = df.iloc[ini:fin]
    xs = np.arange(ini, fin) - ie                # 0 = vela de la entrada

    fig, (ax, ar) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1.1]})
    for x, o, h, l, c in zip(xs, v["Open"], v["High"], v["Low"], v["Close"]):
        col = verde if c >= o else rojo
        ax.vlines(x, l, h, color=col, linewidth=1)
        ax.add_patch(Rectangle((x - 0.31, min(o, c)), 0.62, abs(c - o) or (h - l) * 0.02 or 1e-9,
                               color=col, zorder=3))
    ax.plot(xs, v["ema_l"], color="#f59e0b", lw=1.3, label=f"EMA{EMA_LENTA}")

    # La entrada es al cierre de la vela 0 (borde derecho); el vencimiento, al cierre de la vela k.
    ax.axvline(0.5, color=gris, lw=1, ls=":")
    ax.axhline(precio, color=gris, lw=0.8, ls=":")
    ax.plot(0.5, precio, "^" if lado > 0 else "v", ms=14, color=azul, mec="white", zorder=6,
            label="Entrada")
    sentido = "CALL (sube)" if lado > 0 else "PUT (baja)"
    hora = (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
    titulo = f"{nombre_par(par)} · {estrategia} · {sentido} · entrada {hora:%Y-%m-%d %H:%M} hora Colombia"
    color_titulo = "black"
    if k is not None and cierre is not None:
        col = {"GANADA": verde, "PERDIDA": rojo}.get(res, gris)
        xk = k - ie + 0.5
        ax.axvspan(0.5, xk, color=col, alpha=0.12, zorder=1)
        ax.axvline(xk, color=col, lw=1.2, ls="--")
        ax.plot(xk, cierre, "o", ms=10, color=col, mec="white", zorder=6, label="Cierre")
        pips = (cierre - precio) * lado / (0.01 if par.endswith("JPY") else 0.0001)
        titulo = (f"{nombre_par(par)} · {estrategia} · {sentido} · {res} a {minutos} min ({pips:+.1f} pips)\n"
                  f"entrada {hora:%Y-%m-%d %H:%M} hora Colombia")
        color_titulo = col
    ax.set_title(titulo, fontsize=11, fontweight="bold", color=color_titulo)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: fmt(par, y)))

    ar.plot(xs, v["rsi2"], color="#7c3aed", lw=1.2)
    ar.axhline(10, color=verde, lw=0.8, ls="--")
    ar.axhline(90, color=rojo, lw=0.8, ls="--")
    ar.plot(0, df["rsi2"].iat[ie], "o", ms=7, color=azul, zorder=5)
    ar.set_ylim(-5, 105)
    ar.set_ylabel("RSI(2)", fontsize=9)
    ar.grid(alpha=0.25)
    cada = 6
    ar.set_xticks(xs[::cada])
    ar.set_xticklabels([f"{t:%H:%M}" for t in v.index.tz_convert("America/Bogota")[::cada]], fontsize=8)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- DATOS ----
def descargar(pares, periodo="5d") -> dict:
    """Descarga velas de 5 min de Yahoo Finance. Devuelve {par: DataFrame}."""
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
        except Exception as e:  # noqa: BLE001
            log.warning("Sin datos para %s: %s", p, e)
    return salida


def solo_velas_cerradas(df: pd.DataFrame, ahora=None) -> pd.DataFrame:
    ahora = ahora or pd.Timestamp.now(tz="UTC")
    cierre = df.index + pd.Timedelta(minutes=MIN_VELA)
    return df[cierre <= ahora]


# ----------------------------------------------------------- INDICADORES ---
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def indicadores(df: pd.DataFrame) -> pd.DataFrame:
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
    g2 = rma(delta.clip(lower=0), 2)
    p2 = rma((-delta).clip(lower=0), 2)
    df["rsi2"] = 100 - 100 / (1 + g2 / p2.replace(0, np.nan))
    df["tend_1h"] = tendencia_1h(df)
    return df


def senal_rsi2(df, i):
    """+1 CALL / -1 PUT / 0: RSI de 2 periodos extremo a favor de la tendencia."""
    c, el, t1h, r2 = (df[k].iat[i] for k in ("Close", "ema_l", "tend_1h", "rsi2"))
    if c > el and t1h > 0 and r2 < 10:
        return 1
    if c < el and t1h < 0 and r2 > 90:
        return -1
    return 0


def tendencia_1h(df):
    """+1 si la última vela de 1 h CERRADA está sobre su EMA50, -1 si está debajo.
    Solo usa velas de 1 h ya cerradas al cierre de cada vela de 5 min (sin mirar el futuro)."""
    c1h = df["Close"].resample("1h").last().dropna()
    e = ema(c1h, EMA_1H)
    signo = np.sign(c1h - e)
    signo.iloc[:EMA_1H] = 0          # sin datos suficientes en las primeras horas
    t = pd.DataFrame({"k": c1h.index + pd.Timedelta(hours=1), "t": signo.values}).sort_values("k")
    cierre5 = pd.DataFrame({"k": df.index + pd.Timedelta(minutes=MIN_VELA)})
    m = pd.merge_asof(cierre5, t, on="k", direction="backward")
    return pd.Series(m["t"].fillna(0).values, index=df.index)


def pivotes(df):
    """Índices de máximos/mínimos pivote y la vela en que quedan confirmados."""
    h, l = df["High"].values, df["Low"].values
    n = len(df)
    altos, bajos = [], []
    for j in range(PIVOTE_IZQ, n - PIVOTE_DER):
        ventana_h = h[j - PIVOTE_IZQ: j + PIVOTE_DER + 1]
        ventana_l = l[j - PIVOTE_IZQ: j + PIVOTE_DER + 1]
        if h[j] == ventana_h.max():
            altos.append(j)
        if l[j] == ventana_l.min():
            bajos.append(j)
    return altos, bajos


# ------------------------------------------------------------- SEÑALES -----
def evaluar_vela(df, i, altos, bajos):
    """Evalúa la vela cerrada i usando SOLO información disponible hasta i.
    Devuelve dict con la señal o None."""
    if i < EMA_LENTA + 10:
        return None
    o, h, l, c = (df[k].iat[i] for k in ("Open", "High", "Low", "Close"))
    er, el, adx, atr, rsi = (df[k].iat[i] for k in ("ema_r", "ema_l", "adx", "atr", "rsi"))
    rango = h - l
    if rango <= 0 or atr <= 0 or np.isnan(adx):
        return None
    cuerpo = abs(c - o)
    vela_fuerte = cuerpo >= 0.5 * rango
    confirmado = i - PIVOTE_DER          # un pivote en j solo se conoce en j + PIVOTE_DER

    t1h = df["tend_1h"].iat[i]
    alcista = er > el and c > el and adx >= ADX_TENDENCIA and (t1h > 0 or not FILTRO_1H)
    bajista = er < el and c < el and adx >= ADX_TENDENCIA and (t1h < 0 or not FILTRO_1H)
    if (er > el and c > el or er < el and c < el) and adx >= ADX_TENDENCIA and not (alcista or bajista):
        return None   # la tendencia de 1 h va en contra

    # ---- 1) Tendencia + Fibonacci
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
            if (l <= f382 and minimo_desde >= f786 and c > o and vela_fuerte and c >= f618):
                return dict(modo="Tendencia + Fibonacci", dir="CALL ⬆️ (sube)", lado=1,
                            precio=c, niveles={"0.382": f382, "0.5": f5, "0.618": f618,
                                               "Invalidación 0.786": f786},
                            impulso=(bot, top))
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
            if (h >= f382 and maximo_desde <= f786 and c < o and vela_fuerte and c <= f618):
                return dict(modo="Tendencia + Fibonacci", dir="PUT ⬇️ (baja)", lado=-1,
                            precio=c, niveles={"0.382": f382, "0.5": f5, "0.618": f618,
                                               "Invalidación 0.786": f786},
                            impulso=(top, bot))
        return None

    # ---- 2) Lateral
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


def en_horario(ts: pd.Timestamp) -> bool:
    return ts.weekday() < 5 and HORA_INICIO_UTC <= ts.hour < HORA_FIN_UTC


def backtest(df: pd.DataFrame) -> dict:
    """% de acierto por modo ('tendencia' / 'lateral') con expiración EXPIRACION_VELAS."""
    df = indicadores(df)
    altos, bajos = pivotes(df)
    c = df["Close"].values
    res = {"tendencia": [0, 0], "lateral": [0, 0]}   # [ganadas, total]
    ultimo = -10**9
    for i in range(EMA_LENTA + 10, len(df) - EXPIRACION_VELAS):
        if not en_horario(df.index[i]) or i - ultimo < EXPIRACION_VELAS:
            continue
        s = evaluar_vela(df, i, altos, bajos)
        if not s:
            continue
        ultimo = i
        k = "tendencia" if s["modo"].startswith("Tendencia") else "lateral"
        dif = (c[i + EXPIRACION_VELAS] - c[i]) * s["lado"]
        if dif == 0:
            continue          # empate: en binarias se devuelve el dinero
        res[k][0] += int(dif > 0)
        res[k][1] += 1
    return {k: {"ganadas": g, "total": t, "winrate": (100 * g / t if t else 0.0)}
            for k, (g, t) in res.items()}


# ------------------------------------------------------------ MENSAJES -----
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


# ------------------------------------------------------------- BUCLE -------
def guardar_resultado(par, s, cierre, gano, ts):
    nuevo = not os.path.exists(ARCHIVO_RESULTADOS)
    with open(ARCHIVO_RESULTADOS, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("fecha_utc,par,estrategia,direccion,entrada,cierre,resultado\n")
        d = "CALL" if s["lado"] > 0 else "PUT"
        f.write(f"{ts:%Y-%m-%d %H:%M},{par},{s['modo']},{d},{s['precio']},{cierre},"
                f"{'EMPATE' if gano is None else ('GANADA' if gano else 'PERDIDA')}\n")


def guardar_vigilancia(par, exp, lado, precio, cierre, ts):
    nuevo = not os.path.exists(ARCHIVO_VIGILANCIA)
    dif = (cierre - precio) * lado
    res = "EMPATE" if dif == 0 else ("GANADA" if dif > 0 else "PERDIDA")
    with open(ARCHIVO_VIGILANCIA, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("fecha_utc,par,expiracion_min,direccion,entrada,cierre,resultado\n")
        f.write(f"{ts:%Y-%m-%d %H:%M},{par},{exp * MIN_VELA},{'CALL' if lado > 0 else 'PUT'},"
                f"{precio},{cierre},{res}\n")
    return res


def resumen_vigilancia():
    if not os.path.exists(ARCHIVO_VIGILANCIA):
        return "\n\n🔍 <b>Vigilancia RSI2</b>: aún sin resultados."
    v = pd.read_csv(ARCHIVO_VIGILANCIA)
    v = v[v["resultado"] != "EMPATE"]
    v["fecha_utc"] = pd.to_datetime(v["fecha_utc"], utc=True)
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    lineas = ["\n\n🔍 <b>Vigilancia RSI2</b> (solo observación, meta ≥56 %):"]
    for (par, e), x in v.groupby(["par", "expiracion_min"]):
        g = (x["resultado"] == "GANADA").sum()
        xh = x[x["fecha_utc"] >= hoy]
        gh = (xh["resultado"] == "GANADA").sum()
        lineas.append(f"{par} {e}m — hoy {gh}/{len(xh)} · total {g}/{len(x)} "
                      f"({100 * g / len(x):.0f}%)")
    return "\n".join(lineas)


# ------------------------------------------- VIGILANCIA D (RSI2 + LORENTZIAN)
def guardar_vig_lz(par, lado, precio, cierre, ts):
    nuevo = not os.path.exists(ARCHIVO_VIG_LZ)
    dif = (cierre - precio) * lado
    res = "EMPATE" if dif == 0 else ("GANADA" if dif > 0 else "PERDIDA")
    with open(ARCHIVO_VIG_LZ, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("fecha_utc,par,expiracion_min,direccion,entrada,cierre,resultado\n")
        f.write(f"{ts:%Y-%m-%d %H:%M},{par},{LZ_EXP_VELAS * MIN_VELA},{'CALL' if lado > 0 else 'PUT'},"
                f"{precio},{cierre},{res}\n")
    return res


def real_vig_lz(par=None):
    """Asertividad real acumulada de la vigilancia D: (ganadas, total) sin empates."""
    if not os.path.exists(ARCHIVO_VIG_LZ):
        return 0, 0
    v = pd.read_csv(ARCHIVO_VIG_LZ)
    v = v[v["resultado"] != "EMPATE"]
    if par:
        v = v[v["par"] == par]
    return int((v["resultado"] == "GANADA").sum()), len(v)


def resumen_vig_lz():
    if not VIG_LZ:
        return ""
    if not os.path.exists(ARCHIVO_VIG_LZ):
        return "\n\n🧠 <b>Vigilancia RSI2 + Lorentzian</b>: aún sin resultados."
    v = pd.read_csv(ARCHIVO_VIG_LZ)
    v = v[v["resultado"] != "EMPATE"]
    v["fecha_utc"] = pd.to_datetime(v["fecha_utc"], utc=True)
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    xh = v[v["fecha_utc"] >= hoy]
    g, gh = (v["resultado"] == "GANADA").sum(), (xh["resultado"] == "GANADA").sum()
    lineas = ["\n\n🧠 <b>Vigilancia RSI2 + Lorentzian</b> (15 min, solo observación, meta ≥56 %):",
              f"Todos los pares — hoy {txt_pct(gh, len(xh))} · total {txt_pct(g, len(v))}"]
    for par, x in v.groupby("par"):
        lineas.append(f"{nombre_par(par)}: {txt_pct((x['resultado'] == 'GANADA').sum(), len(x))}")
    return "\n".join(lineas)


def vigilar_lz(par, df, i, ts, abiertas, vistas):
    """Revisa el resultado de la operación abierta del par y busca una nueva señal D."""
    if par in abiertas:
        vts, vlado, vprecio = abiertas[par]
        if vts not in df.index:
            del abiertas[par]
        else:
            k = df.index.get_loc(vts) + LZ_EXP_VELAS
            if k <= i:
                cierre = df["Close"].iat[k]
                res = guardar_vig_lz(par, vlado, vprecio, cierre, vts)
                icono = {"GANADA": "✅", "PERDIDA": "❌", "EMPATE": "➖"}[res]
                enviar_con_grafico(
                    f"🧠 {icono} <b>{res}</b> · RSI2 + Lorentzian · {nombre_par(par)} · "
                    f"{LZ_EXP_VELAS * MIN_VELA} min\n"
                    f"💵 Entrada {fmt(par, vprecio)} → cierre {fmt(par, cierre)}\n"
                    f"📊 Asertividad real: {txt_pct(*real_vig_lz())} (todos los pares)",
                    df, par=par, lado=vlado, ts=vts, precio=vprecio, minutos=LZ_EXP_VELAS * MIN_VELA,
                    k=k, cierre=cierre, res=res, estrategia="RSI2 + Lorentzian")
                del abiertas[par]
    if par in abiertas or (par, ts) in vistas or not en_horario(ts) or lz is None:
        return
    vistas.add((par, ts))
    lado = senal_rsi2(df, i)
    if not lado:
        return
    crudo = df[["Open", "High", "Low", "Close"]]
    hist = DATOS_59D.get(par)
    if hist is not None and len(hist):
        crudo = pd.concat([hist.loc[hist.index < crudo.index[0], ["Open", "High", "Low", "Close"]], crudo])
    est = lz.estado_actual(crudo)
    if est is None or est["sig"] != lado or not (est["sube"] if lado > 0 else est["baja"]):
        return
    precio = df["Close"].iat[i]
    abiertas[par] = (ts, lado, precio)
    hora_col = (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
    sentido = "arriba ⬆️" if lado > 0 else "abajo ⬇️"
    enviar_con_grafico(
        f"🧠 <b>RSI2 + Lorentzian · {nombre_par(par)} - {sentido}</b>\n"
        f"Zona horaria UTC -5\n\n"
        f"⌛ EXPIRACIÓN: {LZ_EXP_VELAS * MIN_VELA} minutos\n"
        f"⏱️ ENTRADA: {hora_col:%H:%M}\n"
        f"💵 Precio: {fmt(par, precio)}\n\n"
        f"📊 <b>ASERTIVIDAD</b>\n"
        f"• Prueba 3 años (7 pares): 50,6 % · ago-sep 2026: 56-58 %\n"
        f"• Real: {txt_pct(*real_vig_lz(par))} en {nombre_par(par)} · "
        f"{txt_pct(*real_vig_lz())} en total\n\n"
        f"🔍 Vigilancia RSI2 + Lorentzian + kernel: solo observar, NO operar.",
        df, par=par, lado=lado, ts=ts, precio=precio, estrategia="RSI2 + Lorentzian")


def mensaje_resumen():
    if not os.path.exists(ARCHIVO_RESULTADOS):
        return ("📋 <b>Resumen del día</b>\nHoy no hubo señales que pasaran el filtro."
                + resumen_vigilancia() + resumen_vig_lz())
    r = pd.read_csv(ARCHIVO_RESULTADOS)
    r = r[r["resultado"] != "EMPATE"]
    r["fecha_utc"] = pd.to_datetime(r["fecha_utc"], utc=True)
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    lunes = hoy - pd.Timedelta(days=hoy.weekday())

    def linea(x, titulo):
        if x.empty:
            return f"{titulo}: sin señales"
        g = (x["resultado"] == "GANADA").sum()
        return f"{titulo}: {len(x)} señales, {g} ✅ {len(x) - g} ❌ ({100 * g / len(x):.0f}%)"

    dia = r[r["fecha_utc"] >= hoy]
    partes = [linea(dia, "Hoy"), linea(r[r["fecha_utc"] >= lunes], "Semana"),
              linea(r, "Total desde el inicio")]
    if not dia.empty:
        por_par = dia.groupby("par")["resultado"].apply(
            lambda v: f"{(v == 'GANADA').sum()}/{len(v)}")
        partes.append("\nPor par hoy: " + ", ".join(f"{k} {v}" for k, v in por_par.items()))
    return "📋 <b>Resumen del día</b>\n" + "\n".join(partes) + resumen_vigilancia() + resumen_vig_lz()


def backtest_vig(df, expiraciones):
    """Asertividad histórica de RSI2 por expiración (sin contar empates)."""
    df = indicadores(df)
    c = df["Close"]
    lado = np.where((c > df["ema_l"]) & (df["tend_1h"] > 0) & (df["rsi2"] < 10), 1,
                    np.where((c < df["ema_l"]) & (df["tend_1h"] < 0) & (df["rsi2"] > 90), -1, 0))
    cv, out = c.values, {}
    for e in expiraciones:
        g = t = 0
        ultimo = -10**9
        for i in np.flatnonzero(lado):
            if i + e >= len(df) or i - ultimo < e or i < EMA_LENTA + 10 or not en_horario(df.index[i]):
                continue
            ultimo = i
            dif = (cv[i + e] - cv[i]) * lado[i]
            if dif == 0:
                continue
            g += int(dif > 0)
            t += 1
        out[e] = (g, t)
    return out


def real_vig(par, e):
    """Asertividad real acumulada en vigilancia: (ganadas, total) sin empates."""
    if not os.path.exists(ARCHIVO_VIGILANCIA):
        return 0, 0
    v = pd.read_csv(ARCHIVO_VIGILANCIA)
    v = v[(v["par"] == par) & (v["expiracion_min"] == e * MIN_VELA) & (v["resultado"] != "EMPATE")]
    return int((v["resultado"] == "GANADA").sum()), len(v)


def txt_pct(g, t):
    return f"{g}/{t} ({100 * g / t:.0f}%)" if t else "aún sin datos"


def nombre_par(par):
    return f"{par[:3]}/{par[3:]}"


TABLA_VIG = {}
DATOS_59D = {}          # historial de 59 días por par (lo necesita el Lorentzian), se renueva cada hora


def calcular_tabla():
    datos = descargar(PARES, periodo="59d")
    DATOS_59D.update(datos)
    for p, df in datos.items():
        if p in VIGILANCIA and len(df) > EMA_LENTA + 50:
            TABLA_VIG[p] = backtest_vig(df, VIGILANCIA[p])
    return {p: backtest(df) for p, df in datos.items() if len(df) > EMA_LENTA + 50}


def aprobado(stats_modo):
    return stats_modo["total"] >= MIN_OPERACIONES and stats_modo["winrate"] >= MIN_WINRATE


def main():
    fin = time.time() + DURACION_MINUTOS * 60
    tabla = calcular_tabla()
    ultima_tabla = time.time()
    if MENSAJE_INICIO:
        enviar(mensaje_ranking(tabla))

    enviadas = {}      # (par, ts_vela) -> dict con la señal para verificar
    ya_vistas = set()
    resumen_enviado = set()
    vig_abiertas = {}   # (par, exp) -> (ts, lado, precio)
    vig_vistas = set()
    lz_abiertas = {}    # par -> (ts, lado, precio) de la vigilancia D
    lz_vistas = set()

    while time.time() < fin:
        # Espera al cierre de la siguiente vela de 5 min (+25 s de margen)
        ahora = time.time()
        siguiente = (ahora // (MIN_VELA * 60) + 1) * MIN_VELA * 60 + 25
        time.sleep(max(1, siguiente - ahora))

        ahora_ts = pd.Timestamp.now(tz="UTC")
        dia = ahora_ts.date()
        if (ahora_ts.weekday() < 5 and ahora_ts.hour >= HORA_FIN_UTC and dia not in resumen_enviado
                and ahora_ts.hour < HORA_FIN_UTC + 1):
            resumen_enviado.add(dia)
            enviar(mensaje_resumen())

        if time.time() - ultima_tabla > 3600:          # recalcula estadísticas cada hora
            try:
                tabla = calcular_tabla()
                ultima_tabla = time.time()
            except Exception as e:  # noqa: BLE001
                log.warning("Error recalculando backtest: %s", e)

        try:
            datos = descargar(PARES, periodo="5d")
        except Exception as e:  # noqa: BLE001
            log.warning("Error descargando datos: %s", e)
            continue

        for par, df in datos.items():
            df = solo_velas_cerradas(df)
            if len(df) < EMA_LENTA + 50:
                continue
            df = indicadores(df)
            i = len(df) - 1
            ts = df.index[i]

            # Verificar resultados de señales anteriores
            for clave, s in list(enviadas.items()):
                if clave[0] != par or clave[1] not in df.index:
                    continue
                k = df.index.get_loc(clave[1]) + EXPIRACION_VELAS
                if k <= i:
                    cierre = df["Close"].iat[k]
                    dif = (cierre - s["precio"]) * s["lado"]
                    gano = (dif > 0) if dif != 0 else None
                    etiqueta = "➖ EMPATE" if gano is None else ("✅ GANADA" if gano else "❌ PERDIDA")
                    enviar(f"{etiqueta} — {par} {s['dir']}\n"
                           f"Entrada {fmt(par, s['precio'])} → cierre {fmt(par, cierre)}")
                    guardar_resultado(par, s, cierre, gano, clave[1])
                    del enviadas[clave]

            # ---- Modo vigilancia (RSI2): verificar resultados y buscar nuevas señales
            for (vp, ve), (vts, vlado, vprecio) in list(vig_abiertas.items()):
                if vp != par or vts not in df.index:
                    continue
                k = df.index.get_loc(vts) + ve
                if k <= i:
                    cierre = df["Close"].iat[k]
                    res = guardar_vigilancia(par, ve, vlado, vprecio, cierre, vts)
                    icono = {"GANADA": "✅", "PERDIDA": "❌", "EMPATE": "➖"}[res]
                    enviar_con_grafico(
                        f"🔍 {icono} <b>{res}</b> · {nombre_par(par)} · {ve * MIN_VELA} min\n"
                        f"💵 Entrada {fmt(par, vprecio)} → cierre {fmt(par, cierre)}\n"
                        f"📊 Asertividad real: {txt_pct(*real_vig(par, ve))}",
                        df, par=par, lado=vlado, ts=vts, precio=vprecio,
                        minutos=ve * MIN_VELA, k=k, cierre=cierre, res=res)
                    del vig_abiertas[(vp, ve)]
            if par in VIGILANCIA and (par, ts) not in vig_vistas and en_horario(ts):
                vig_vistas.add((par, ts))
                libres = [e for e in VIGILANCIA[par] if (par, e) not in vig_abiertas]
                lado = senal_rsi2(df, i) if libres else 0
                if lado:
                    precio = df["Close"].iat[i]
                    for e in libres:
                        vig_abiertas[(par, e)] = (ts, lado, precio)
                    hora_col = (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
                    pcts = []
                    for e in libres:
                        hg, ht = TABLA_VIG.get(par, {}).get(e, (0, 0))
                        pcts.append(f"• {e * MIN_VELA} min: histórico {txt_pct(hg, ht)} · "
                                    f"real {txt_pct(*real_vig(par, e))}")
                    flecha = "🔺🔺" if lado > 0 else "🔻🔻"
                    sentido = "arriba ⬆️" if lado > 0 else "abajo ⬇️"
                    enviar_con_grafico(
                        f"{flecha} <b>Par de divisas: {nombre_par(par)} - {sentido}</b>\n"
                        f"Zona horaria UTC -5\n\n"
                        f"⌛ EXPIRACIÓN: {' y '.join(str(e * MIN_VELA) for e in libres)} minutos\n"
                        f"⏱️ ENTRADA: {hora_col:%H:%M}\n"
                        f"💵 Precio: {fmt(par, precio)}\n\n"
                        f"📊 <b>ASERTIVIDAD</b>\n" + "\n".join(pcts) + "\n\n"
                        f"🔍 Modo vigilancia (RSI2): solo observar, NO operar.",
                        df, par=par, lado=lado, ts=ts, precio=precio)

            # ---- Vigilancia D: RSI2 + Lorentzian + kernel (todos los pares, 15 min)
            if VIG_LZ:
                try:
                    vigilar_lz(par, df, i, ts, lz_abiertas, lz_vistas)
                except Exception as e:  # noqa: BLE001
                    log.warning("Vigilancia Lorentzian %s: %s", par, e)

            if (par, ts) in ya_vistas or not en_horario(ts):
                continue
            ya_vistas.add((par, ts))
            if any(c[0] == par for c in enviadas):      # una señal abierta por par
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
    if len(sys.argv) > 1 and sys.argv[1] == "resumen":
        enviar(mensaje_resumen())
    elif len(sys.argv) > 1 and sys.argv[1] == "backtest":
        enviar(mensaje_ranking(calcular_tabla()))
    else:
        main()
