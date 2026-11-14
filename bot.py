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
# Filtro de horario (hora Colombia/Perú, UTC-5) para las dos vigilancias.
# Solo se AVISAN por Telegram las señales cuya ENTRADA cae en estas horas (2 = de 2:00 a 2:59).
# Las de otras horas se siguen midiendo EN SILENCIO (se guardan en el CSV) para comprobar
# con datos reales si el filtro de verdad funciona.
# Origen: resultados reales del 19-oct al 13-nov-2026 -> 2 am 73 %, 3 am 92 %, 6 am 70 %,
# 2 pm 69 %; en cambio 10 am solo 25 %. Son pocas operaciones: hay que confirmarlo.
# Para cambiar las horas: edita este valor o pon HORAS_BUENAS en bot.yml. Vacío = todas las horas.
HORAS_BUENAS = sorted({int(_h) for _h in os.getenv("HORAS_BUENAS", "2,3,6,14").split(",")
                       if _h.strip()})

# Aprendizaje: el bot guarda el contexto de cada operación de vigilancia (hora, fuerza del RSI2,
# ADX, volatilidad, distancia a la EMA200, noticias...) y aprende qué condiciones ganan o pierden.
# Aprende SOLO de las operaciones reales desde que empezó el bot (19-oct-2026), no de años pasados.
# Para no dejarse engañar por una racha, una condición solo queda BUENA o MALA si se cumple en la
# mitad antigua Y en la mitad reciente de esas operaciones reales.
# Las señales con una condición MALA confirmada no se avisan (se siguen midiendo en silencio).
APRENDIZAJE = os.getenv("APRENDIZAJE", "1") == "1"
ARCHIVO_APRENDIZAJE = "aprendizaje.csv"
ARCHIVO_HISTORIAL = "aprendizaje_historial.csv"
USAR_HISTORIAL = os.getenv("USAR_HISTORIAL", "0") == "1"   # años pasados: apagado por decisión de Brandy
MIN_REAL_APRENDER = 20        # operaciones reales mínimas para juzgar una condición
APRENDIDO = {}                # lo aprendido, se recalcula cada hora con aprender()
CONTEXTOS = {}                # (estrategia, par, ts) -> contexto de la entrada

# Estrategias nuevas (vigilancia, solo observar): RSI2 + un segundo filtro, cada una por separado.
#   SR  = RSI2 extremo justo en un soporte (CALL) o una resistencia (PUT) del último día.
#   ENV = RSI2 extremo en la vela anterior y luego una vela envolvente en sentido contrario.
#   DIV = divergencia: el precio hace un mínimo más bajo (máximo más alto) y el RSI2 no lo confirma.
# Todo el horario del bot (2 am a 3 pm), los 7 pares, expiraciones de 15 y 30 min.
NUEVAS = {
    "SR": ("🧱", "RSI2 + Soportes/Resistencias"),
    "ENV": ("🕯️", "RSI2 + Vela envolvente"),
    "DIV": ("🔀", "RSI2 + Divergencia"),
}
NUEVAS_ACTIVAS = [x.strip().upper() for x in os.getenv("NUEVAS_ESTRATEGIAS", "SR,ENV,DIV").split(",")
                  if x.strip().upper() in NUEVAS]
NUEVAS_EXP_VELAS = [int(x) for x in os.getenv("NUEVAS_EXP_VELAS", "3,6").split(",") if x.strip()]
ARCHIVO_NUEVAS = "estrategias_nuevas.csv"
SR_VENTANA = 288              # niveles de los pivotes del último día (288 velas de 5 min)
SR_TOLERANCIA = 0.3           # cuánto puede faltar para "tocar" el nivel, en ATR
SR_TOQUES_MIN = int(os.getenv("SR_TOQUES_MIN", "2"))   # el nivel debe haberse tocado 2+ veces
DIV_VENTANA = (5, 40)         # el mínimo/máximo anterior se busca entre 5 y 40 velas atrás

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
_ULTIMO_ENVIO = [0.0]


def _post_telegram(metodo, **kwargs):
    """Envía a Telegram con 1,1 s entre mensajes (para no pasarse del límite del chat).
    Si Telegram pide esperar (error 429), espera lo que indica y reintenta una vez."""
    for intento in range(2):
        falta = 1.1 - (time.time() - _ULTIMO_ENVIO[0])
        if falta > 0:
            time.sleep(falta)
        _ULTIMO_ENVIO[0] = time.time()
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{metodo}", **kwargs)
        if r.status_code != 429 or intento:
            return r
        try:
            espera = int(r.json().get("parameters", {}).get("retry_after", 5))
        except Exception:  # noqa: BLE001
            espera = 5
        log.warning("Telegram pidió esperar %s s; reintento.", espera)
        time.sleep(min(espera, 60) + 1)
    return r


def enviar(texto: str) -> None:
    if len(texto) > 4000:          # límite de Telegram: se corta en un salto de línea
        texto = texto[:texto.rfind("\n", 0, 3980)] + "\n…"
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("\n[MODO PRUEBA - sin Telegram]\n" + texto + "\n")
        return
    try:
        r = _post_telegram(
            "sendMessage",
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
        r = _post_telegram(
            "sendPhoto",
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
                estrategia="RSI2", nivel=None):
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
    if nivel is not None:
        ax.axhline(nivel, color="#8b5cf6", lw=1.4, ls="--",
                   label="Soporte" if lado > 0 else "Resistencia")

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


def hora_entrada(ts: pd.Timestamp) -> int:
    """Hora Colombia/Perú de la ENTRADA (cierre de la vela de 5 min que abre en ts)."""
    return (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota").hour


def en_horas_buenas(ts: pd.Timestamp) -> bool:
    return not HORAS_BUENAS or hora_entrada(ts) in HORAS_BUENAS


def texto_horas():
    if not HORAS_BUENAS:
        return "todas las horas"
    return ", ".join(f"{h}:00-{h}:59" for h in HORAS_BUENAS)


def separar_horario(v: pd.DataFrame):
    """Divide un CSV de vigilancia en (dentro del horario bueno, fuera)."""
    if v.empty:
        return v, v
    horas = (pd.to_datetime(v["fecha_utc"], utc=True) + pd.Timedelta(minutes=MIN_VELA)) \
        .dt.tz_convert("America/Bogota").dt.hour
    dentro = horas.isin(HORAS_BUENAS) if HORAS_BUENAS else pd.Series(True, index=v.index)
    return v[dentro.values], v[~dentro.values]


# ---------------------------------------------------------- APRENDIZAJE ----
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
VARIABLES = {"hora": "hora", "dia": "día", "lado": "dirección", "rsi2": "fuerza RSI2",
             "adx": "ADX", "volatilidad": "volatilidad", "dist_ema": "distancia a EMA200",
             "noticias": "hora de noticias", "tendencia": "tendencia", "par": "par"}
DATOS_ENTRADA = ["hora", "dia", "lado", "rsi2", "adx", "vol_rel", "dist_ema", "noticias", "tendencia"]
DATOS_SALIDA = ["que_paso", "a_favor_pips", "en_contra_pips"]      # qué hizo el precio después
COLUMNAS_APRENDIZAJE = (["fecha_utc", "estrategia", "par", "expiracion_min", "avisada", "motivo",
                         "resultado"] + DATOS_ENTRADA + DATOS_SALIDA)


def es_hora_noticias(entrada: pd.Timestamp) -> bool:
    """8:15-9:00 y 9:45-10:15 hora de Nueva York (salen casi todos los datos fuertes)."""
    t = entrada.tz_convert("America/New_York")
    m = t.hour * 60 + t.minute
    return (8 * 60 + 15 <= m <= 9 * 60) or (9 * 60 + 45 <= m <= 10 * 60 + 15)


def contexto(df, i, lado) -> dict:
    """Datos de la vela de la señal (índice i, ya con indicadores) que el bot usa para aprender."""
    entrada = df.index[i] + pd.Timedelta(minutes=MIN_VELA)
    local = entrada.tz_convert("America/Bogota")
    atr = df["atr"].iat[i]
    mediana = df["atr"].iloc[max(0, i - 288):i + 1].median()     # ATR típico del último día
    r2 = df["rsi2"].iloc[max(0, i - 1):i + 1]                   # vela de la señal y la anterior
    sobre = df["Close"].iat[i] > df["ema_l"].iat[i]
    t1h = df["tend_1h"].iat[i]
    a_favor = (lado > 0 and sobre and t1h > 0) or (lado < 0 and not sobre and t1h < 0)
    en_contra = (lado > 0 and not sobre and t1h < 0) or (lado < 0 and sobre and t1h > 0)
    return {
        "hora": int(local.hour),
        "dia": int(local.dayofweek),
        "lado": "CALL" if lado > 0 else "PUT",
        # distancia al extremo del RSI2 (0 = lo más extremo), mirando la vela de la señal y la anterior
        "rsi2": float(r2.min() if lado > 0 else 100 - r2.max()),
        "adx": float(df["adx"].iat[i]),
        "vol_rel": float(atr / mediana) if mediana and mediana > 0 else np.nan,
        "dist_ema": float(abs(df["Close"].iat[i] - df["ema_l"].iat[i]) / atr) if atr > 0 else np.nan,
        "noticias": bool(es_hora_noticias(entrada)),
        "tendencia": "a favor" if a_favor else ("en contra" if en_contra else "mixta"),
    }


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


def grupos(ctx: dict) -> dict:
    """Pasa un contexto a grupos con nombre: es lo que se compara al aprender."""
    g = {}
    if _num(ctx.get("hora")) is not None:
        g["hora"] = f"{int(_num(ctx['hora']))} h"
    if _num(ctx.get("dia")) is not None:
        g["dia"] = DIAS[int(_num(ctx["dia"]))]
    if ctx.get("lado") in ("CALL", "PUT"):
        g["lado"] = ctx["lado"]
    v = _num(ctx.get("rsi2"))
    if v is not None:
        g["rsi2"] = ("muy extremo (<3)" if v < 3 else "extremo (3-6)" if v < 6 else
                     "justo (6-10)" if v < 10 else "fuera del extremo (≥10)")
    v = _num(ctx.get("adx"))
    if v is not None:
        g["adx"] = "débil (<20)" if v < 20 else ("normal (20-30)" if v < 30 else "fuerte (>30)")
    v = _num(ctx.get("vol_rel"))
    if v is not None:
        g["volatilidad"] = "baja" if v < 0.8 else ("normal" if v <= 1.25 else "alta")
    v = _num(ctx.get("dist_ema"))
    if v is not None:
        g["dist_ema"] = ("cerca (<1 ATR)" if v < 1 else
                         ("media (1-3 ATR)" if v <= 3 else "lejos (>3 ATR)"))
    n = ctx.get("noticias")
    if isinstance(n, (bool, np.bool_)) or str(n) in ("True", "False"):
        g["noticias"] = "sí" if str(n) == "True" else "no"
    if ctx.get("tendencia") in ("a favor", "en contra", "mixta"):
        g["tendencia"] = ctx["tendencia"]
    if ctx.get("par"):
        g["par"] = nombre_par(ctx["par"])
    return g


def clave_de(estrategia, par, exp_min):
    """Grupo de operaciones que se aprende junto. RSI2: por par y expiración. Lorentzian: todos
    los pares juntos. Estrategias nuevas: todos los pares juntos, por expiración."""
    if estrategia == "RSI2":
        return f"RSI2 {par} {int(exp_min)}"
    if estrategia in NUEVAS:
        return f"{estrategia} {int(exp_min)}"
    return "LZ"


def nombre_clave(clave):
    if clave == "LZ":
        return "RSI2 + Lorentzian"
    partes = clave.split()
    if partes[0] in NUEVAS:
        return f"{NUEVAS[partes[0]][1]} {partes[1]}m"
    return f"RSI2 {nombre_par(partes[1])} {partes[2]}m"


def guardar_aprendizaje(estrategia, par, exp_min, ts, ctx, avisada, motivo, res, analisis=None):
    nuevo = not os.path.exists(ARCHIVO_APRENDIZAJE)
    fila = {"fecha_utc": f"{ts:%Y-%m-%d %H:%M}", "estrategia": estrategia, "par": par,
            "expiracion_min": int(exp_min), "avisada": bool(avisada), "motivo": motivo,
            "resultado": res, **{k: ctx.get(k) for k in DATOS_ENTRADA},
            **{k: (analisis or {}).get(k) for k in DATOS_SALIDA}}
    pd.DataFrame([fila], columns=COLUMNAS_APRENDIZAJE).to_csv(
        ARCHIVO_APRENDIZAJE, mode="a", header=nuevo, index=False)


# ------------------------------------------------- QUÉ PASÓ EN LA OPERACIÓN --
CONSEJOS = {
    "Casi empate": "pérdidas por muy poco: es mala suerte más que un error de la señal",
    "Vela brusca en contra": "posible noticia: conviene evitar las horas de noticias",
    "Rompió el nivel": "el soporte/resistencia no aguantó: pedir niveles más tocados",
    "Iba ganando y se dio vuelta": "llega a ir a favor: una expiración más corta podría servir",
    "En contra desde el inicio": "entrada muy temprana: esperar una vela de confirmación",
    "Fue contra la tendencia": "conviene filtrar las señales contra la tendencia de 1 h",
    "Siguió en contra poco a poco": "el impulso continuó: el extremo del RSI2 no bastó",
}


def pip(par):
    return 0.01 if par.endswith("JPY") else 0.0001


def analizar_operacion(df, ie, k, lado, precio, par, nivel=None):
    """Qué hizo el precio entre la entrada (cierre de la vela ie) y el vencimiento (cierre de k).
    Devuelve dict con 'que_paso' (solo en pérdidas), cuánto llegó a ir a favor y en contra (pips)
    y un texto corto para el mensaje."""
    tramo = df.iloc[ie + 1:k + 1]
    if tramo.empty:
        return {"que_paso": "", "a_favor_pips": np.nan, "en_contra_pips": np.nan, "texto": ""}
    atr = df["atr"].iat[ie] if df["atr"].iat[ie] > 0 else pip(par)
    p = pip(par)
    a_favor = float(((tramo["High"] - precio) if lado > 0 else (precio - tramo["Low"])).max())
    en_contra = float(((precio - tramo["Low"]) if lado > 0 else (tramo["High"] - precio)).max())
    final = float((df["Close"].iat[k] - precio) * lado)
    primera = float((tramo["Close"].iat[0] - precio) * lado)
    rangos = tramo["High"] - tramo["Low"]
    jg = rangos.values.argmax()
    brusca = (rangos.iat[jg] >= 2.5 * atr
              and (tramo["Close"].iat[jg] - tramo["Open"].iat[jg]) * lado < 0)
    rompio = nivel is not None and bool(((tramo["Close"] < nivel - SR_TOLERANCIA * atr).any()) if lado > 0
                                        else ((tramo["Close"] > nivel + SR_TOLERANCIA * atr).any()))
    ctx = contexto(df, ie, lado)
    datos = {"a_favor_pips": round(max(a_favor, 0) / p, 1), "en_contra_pips": round(max(en_contra, 0) / p, 1)}
    if final >= 0:
        return {**datos, "que_paso": "", "texto": ""}
    if abs(final) <= 0.1 * atr:
        q = "Casi empate"
    elif brusca:
        q = "Vela brusca en contra"
    elif rompio:
        q = "Rompió el nivel"
    elif a_favor >= 0.5 * atr:
        q = "Iba ganando y se dio vuelta"
    elif primera < 0 and a_favor < 0.2 * atr:
        q = "En contra desde el inicio"
    elif ctx["tendencia"] == "en contra":
        q = "Fue contra la tendencia"
    else:
        q = "Siguió en contra poco a poco"
    texto = (f"🔎 <b>Qué pasó:</b> {q.lower()} · llegó a ir +{datos['a_favor_pips']} pips a favor "
             f"y -{datos['en_contra_pips']} en contra · perdió por {abs(final) / p:.1f} pips")
    return {**datos, "que_paso": q, "texto": texto}


def wilson(g, n, z=1.96):
    """Rango probable (95 %) del acierto verdadero con g ganadas de n."""
    if n == 0:
        return 0.0, 1.0
    p = g / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - m, c + m


def estado_regla(g, n, gv, nv, gn, nn, he_g, he_n, hp_g, hp_n):
    """'buena', 'mala' u 'observación'. Con historia de 3 años, tiene que cumplirse en los reales
    y en las dos partes de la historia. Sin historia, en la mitad vieja y la reciente de los reales."""
    if n < MIN_REAL_APRENDER:
        return "observación"
    p = g / n
    if he_n >= 100 and hp_n >= 100:
        e, q = he_g / he_n, hp_g / hp_n
        if p >= 0.56 and e >= 0.54 and q >= 0.54:
            return "buena"
        if p <= 0.44 and e <= 0.48 and q <= 0.48:
            return "mala"
        return "observación"
    if n < 40 or nv < 15 or nn < 15:
        return "observación"
    lb, ub = wilson(g, n)
    if lb >= 0.50 and gv / nv >= 0.54 and gn / nn >= 0.54:
        return "buena"
    if ub <= 0.50 and gv / nv <= 0.46 and gn / nn <= 0.46:
        return "mala"
    return "observación"


def cargar_reales() -> pd.DataFrame:
    """Todas las operaciones reales de vigilancia (sin empates) con sus grupos en columnas g_*.
    Las de antes del aprendizaje solo tienen hora, día y dirección."""
    partes = []
    for estrategia, archivo in (("RSI2", ARCHIVO_VIGILANCIA), ("LZ", ARCHIVO_VIG_LZ),
                                (None, ARCHIVO_NUEVAS)):
        if os.path.exists(archivo):
            x = pd.read_csv(archivo)
            if estrategia:                    # el archivo de las nuevas ya trae la columna
                x["estrategia"] = estrategia
            partes.append(x[["fecha_utc", "estrategia", "par", "expiracion_min", "direccion",
                             "resultado"]])
    if not partes:
        return pd.DataFrame()
    v = pd.concat(partes, ignore_index=True)
    v = v[v["resultado"] != "EMPATE"].reset_index(drop=True)
    if v.empty:
        return v
    llave = ["estrategia", "par", "expiracion_min", "fecha_utc"]
    if os.path.exists(ARCHIVO_APRENDIZAJE):
        a = pd.read_csv(ARCHIVO_APRENDIZAJE).drop_duplicates(llave, keep="last")
        a = a.reindex(columns=llave + DATOS_ENTRADA).drop(columns=["lado"])
        v = v.merge(a, on=llave, how="left")
    entrada = pd.to_datetime(v["fecha_utc"], utc=True) + pd.Timedelta(minutes=MIN_VELA)
    local = entrada.dt.tz_convert("America/Bogota")
    v["entrada"] = entrada
    v["hora"] = v["hora"].fillna(local.dt.hour) if "hora" in v else local.dt.hour
    v["dia"] = v["dia"].fillna(local.dt.dayofweek) if "dia" in v else local.dt.dayofweek
    v["lado"] = v["direccion"]
    v["gano"] = (v["resultado"] == "GANADA").astype(int)
    v["clave"] = [clave_de(e, p, m) for e, p, m in zip(v["estrategia"], v["par"], v["expiracion_min"])]
    filas = []
    for r in v.to_dict("records"):
        if r["estrategia"] == "RSI2":
            r = {**r, "par": None}            # en RSI2 cada par ya se aprende por separado
        filas.append(grupos(r))
    g = pd.DataFrame(filas, index=v.index).add_prefix("g_")
    return v.join(g)


def aprender() -> dict:
    """Revisa todas las condiciones con los datos reales (y la historia de 3 años si existe)."""
    reales = cargar_reales()
    hist = pd.DataFrame(columns=["clave", "variable", "grupo", "periodo", "ganadas", "total"])
    if USAR_HISTORIAL and os.path.exists(ARCHIVO_HISTORIAL):
        hist = pd.read_csv(ARCHIVO_HISTORIAL)
    reglas = []
    if not reales.empty:
        for clave, x in reales.groupby("clave"):
            base = x["gano"].mean()
            mitad = x["entrada"].sort_values().iloc[len(x) // 2]
            hc = hist[hist["clave"] == clave]
            for var in VARIABLES:
                col = f"g_{var}"
                if col not in x:
                    continue
                for grupo, y in x.dropna(subset=[col]).groupby(col):
                    vieja, nueva = y[y["entrada"] < mitad], y[y["entrada"] >= mitad]
                    h = hc[(hc["variable"] == var) & (hc["grupo"] == grupo)]
                    he, hp = h[h["periodo"] == "entreno"], h[h["periodo"] == "prueba"]
                    fila = dict(clave=clave, variable=var, grupo=grupo,
                                g=int(y["gano"].sum()), n=len(y), base=base,
                                gv=int(vieja["gano"].sum()), nv=len(vieja),
                                gn=int(nueva["gano"].sum()), nn=len(nueva),
                                he_g=int(he["ganadas"].sum()), he_n=int(he["total"].sum()),
                                hp_g=int(hp["ganadas"].sum()), hp_n=int(hp["total"].sum()))
                    fila["estado"] = estado_regla(*(fila[k] for k in (
                        "g", "n", "gv", "nv", "gn", "nn", "he_g", "he_n", "hp_g", "hp_n")))
                    reglas.append(fila)
    r = pd.DataFrame(reglas)
    if not r.empty:
        # cuánto se aleja del promedio, pesado por cantidad de operaciones (para ordenar pistas)
        r["peso"] = (r["g"] / r["n"] - r["base"]).abs() * np.sqrt(r["n"])
    return {"reglas": r, "n_reales": len(reales)}


def txt_hist(fila):
    if not USAR_HISTORIAL:
        return ""
    n = fila["he_n"] + fila["hp_n"]
    return f" · 3 años {100 * (fila['he_g'] + fila['hp_g']) / n:.0f} %" if n else " · sin historia"


def consultar(clave, ctx):
    """Lo aprendido que aplica a una señal: (malas, buenas, pistas) como listas de filas."""
    r = APRENDIDO.get("reglas")
    if r is None or r.empty:
        return [], [], []
    g = grupos(ctx)
    sub = r[r["clave"] == clave]
    if sub.empty:                       # estrategia sin nada aprendido todavía
        return [], [], []
    sub = sub[np.array([g.get(v) == gr for v, gr in zip(sub["variable"], sub["grupo"])], dtype=bool)]
    malas = sub[sub["estado"] == "mala"].to_dict("records")
    buenas = sub[sub["estado"] == "buena"].to_dict("records")
    pistas = sub[sub["n"] >= 5].sort_values("peso", ascending=False).head(2).to_dict("records")
    return malas, buenas, pistas


def texto_aprendizaje(clave, ctx):
    """Líneas para el mensaje de la señal: lo que pasó antes en casos parecidos."""
    if not APRENDIZAJE:
        return ""
    _, buenas, pistas = consultar(clave, ctx)
    lineas = []
    for f in pistas:
        lineas.append(f"• {VARIABLES[f['variable']]} {f['grupo']}: real {txt_pct(f['g'], f['n'])}"
                      f"{txt_hist(f)}")
    if buenas:
        lineas.append("⭐ Condición buena confirmada: " + ", ".join(
            f"{VARIABLES[f['variable']]} {f['grupo']}" for f in buenas))
    return ("\n🧠 <b>Casos parecidos</b>\n" + "\n".join(lineas) + "\n") if lineas else ""


def decidir_aviso(clave, ts, ctx, usar_horario=True):
    """(avisar, motivo). Motivo: '' si se avisa, 'horario' o 'aprendizaje' si se mide en silencio.
    Las estrategias nuevas usan usar_horario=False: avisan en todo el horario del bot."""
    if usar_horario and not en_horas_buenas(ts):
        return False, "horario"
    if APRENDIZAJE:
        malas, _, _ = consultar(clave, ctx)
        if malas:
            log.info("%s: condición mala aprendida (%s), se mide en silencio.", clave,
                     ", ".join(f"{m['variable']}={m['grupo']}" for m in malas))
            return False, "aprendizaje"
    return True, ""


def actualizar_aprendizaje():
    """Recalcula lo aprendido (al empezar y cada hora). Si falla, el bot sigue igual que antes."""
    if not APRENDIZAJE:
        return
    try:
        APRENDIDO.clear()
        APRENDIDO.update(aprender())
        r = APRENDIDO["reglas"]
        if not r.empty:
            log.info("Aprendizaje: %d condiciones revisadas, %d buenas y %d malas confirmadas.",
                     len(r), int((r["estado"] == "buena").sum()), int((r["estado"] == "mala").sum()))
    except Exception as e:  # noqa: BLE001
        APRENDIDO.clear()
        log.warning("No se pudo actualizar el aprendizaje: %s", e)


def resumen_aprendizaje():
    if not APRENDIZAJE:
        return ""
    try:
        ap = aprender()
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo calcular el aprendizaje: %s", e)
        return ""
    r = ap["reglas"]
    lineas = ["\n\n🧠 <b>Aprendizaje</b> (" + ("reales + prueba de 3 años" if USAR_HISTORIAL else
                                             "solo operaciones reales desde que empezó el bot") + "):"]
    if os.path.exists(ARCHIVO_APRENDIZAJE):
        a = pd.read_csv(ARCHIVO_APRENDIZAJE)
        a = a[pd.to_datetime(a["fecha_utc"], utc=True) >= pd.Timestamp.now(tz="UTC").normalize()]
        perd = a[a["resultado"] == "PERDIDA"]
        if len(perd):
            g = pd.DataFrame([grupos(x) for x in perd.to_dict("records")])
            comunes = []
            for var in ("volatilidad", "dist_ema", "rsi2", "adx", "hora", "lado"):
                if var in g and g[var].notna().any():
                    top = g[var].value_counts()
                    comunes.append((top.iloc[0] / len(perd), f"{VARIABLES[var]} {top.index[0]} "
                                                             f"({top.iloc[0]} de {len(perd)})"))
            for var in ("tendencia",):
                if var in g and g[var].notna().any():
                    top = g[var].value_counts()
                    comunes.append((top.iloc[0] / len(perd), f"{VARIABLES[var]} {top.index[0]} "
                                                             f"({top.iloc[0]} de {len(perd)})"))
            comunes.sort(reverse=True)
            lineas.append(f"Pérdidas de hoy ({len(perd)}), lo más repetido al entrar: "
                          + ", ".join(t for _, t in comunes[:2]))
            if "que_paso" in perd and perd["que_paso"].notna().any():
                qp = perd["que_paso"].dropna().value_counts()
                lineas.append("Qué pasó en esas pérdidas: " + ", ".join(
                    f"{q.lower()} ({n})" for q, n in qp.head(3).items()))
                lineas.append(f"💡 Lo que más pasó → {CONSEJOS.get(qp.index[0], '')}")
        silencio = int((a["motivo"] == "aprendizaje").sum())
        if silencio:
            lineas.append(f"Señales no avisadas hoy por lo aprendido: {silencio}")
    if r.empty:
        return "\n".join(lineas + ["Aún sin datos para aprender."])
    for estado, titulo in (("buena", "✅ Buenas confirmadas"), ("mala", "❌ Malas confirmadas (no las aviso)")):
        x = r[r["estado"] == estado].sort_values("peso", ascending=False).head(3)
        lineas.append(f"{titulo}: " + ("ninguna todavía" if x.empty else "; ".join(
            f"{nombre_clave(f['clave'])} · {VARIABLES[f['variable']]} {f['grupo']} "
            f"{txt_pct(f['g'], f['n'])}{txt_hist(f)}" for f in x.to_dict("records"))))
    obs = r[(r["estado"] == "observación") & (r["n"] >= 10)].sort_values("peso", ascending=False)
    if len(obs):
        lineas.append("🔎 Pistas sin confirmar:")
        for f in obs.head(3).to_dict("records"):
            lineas.append(f"• {nombre_clave(f['clave'])} · {VARIABLES[f['variable']]} {f['grupo']}: "
                          f"real {txt_pct(f['g'], f['n'])}{txt_hist(f)}")
    # Qué pasó en TODAS las pérdidas desde que se guarda (no solo hoy), por estrategia
    if os.path.exists(ARCHIVO_APRENDIZAJE):
        t = pd.read_csv(ARCHIVO_APRENDIZAJE)
        t = t[(t["resultado"] == "PERDIDA") & t["que_paso"].notna()] if "que_paso" in t else t.iloc[0:0]
        if len(t) >= 5:
            lineas.append("📚 Qué pasó en las pérdidas (acumulado):")
            for estr, x in t.groupby("estrategia"):
                qp = x["que_paso"].value_counts()
                nombre = NUEVAS[estr][1] if estr in NUEVAS else (
                    "RSI2 + Lorentzian" if estr == "LZ" else "RSI2")
                lineas.append(f"• {nombre}: " + ", ".join(
                    f"{q.lower()} {100 * n / len(x):.0f} %" for q, n in qp.head(2).items())
                    + f" (de {len(x)})")
    return "\n".join(lineas)


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
            f"{lineas}\n\n"
            f"⏰ Vigilancias: solo aviso las entradas de {texto_horas()} (hora Colombia/Perú). "
            f"Las demás horas se miden en silencio."
            + (f"\n🆕 Estrategias nuevas ({', '.join(NUEVAS[x][1] for x in NUEVAS_ACTIVAS)}): "
               f"todo el horario, 7 pares, "
               f"{' y '.join(str(e * MIN_VELA) for e in NUEVAS_EXP_VELAS)} min." if NUEVAS_ACTIVAS else ""))


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
    lineas = ["\n\n🔍 <b>Vigilancia RSI2</b> (todas las horas, solo observación, meta ≥56 %):"]
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


def real_horario(archivo, par=None, exp_min=None):
    """Asertividad real SOLO de las entradas en horario bueno: (ganadas, total) sin empates."""
    if not os.path.exists(archivo):
        return 0, 0
    v = pd.read_csv(archivo)
    v = v[v["resultado"] != "EMPATE"]
    if par:
        v = v[v["par"] == par]
    if exp_min:
        v = v[v["expiracion_min"] == exp_min]
    dentro, _ = separar_horario(v)
    return int((dentro["resultado"] == "GANADA").sum()), len(dentro)


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
    lineas = ["\n\n🧠 <b>Vigilancia RSI2 + Lorentzian</b> (15 min, todas las horas, solo observación, "
              "meta ≥56 %):",
              f"Todos los pares — hoy {txt_pct(gh, len(xh))} · total {txt_pct(g, len(v))}"]
    for par, x in v.groupby("par"):
        lineas.append(f"{nombre_par(par)}: {txt_pct((x['resultado'] == 'GANADA').sum(), len(x))}")
    return "\n".join(lineas)


def vigilar_lz(par, df, i, ts, abiertas, vistas):
    """Revisa el resultado de la operación abierta del par y busca una nueva señal D."""
    if par in abiertas:
        vts, vlado, vprecio, avisar, motivo = abiertas[par]
        if vts not in df.index:
            del abiertas[par]
            CONTEXTOS.pop(("LZ", par, vts), None)
        else:
            k = df.index.get_loc(vts) + LZ_EXP_VELAS
            if k <= i:
                cierre = df["Close"].iat[k]
                res = guardar_vig_lz(par, vlado, vprecio, cierre, vts)
                ctx = CONTEXTOS.pop(("LZ", par, vts), None) or {
                    **contexto(df, df.index.get_loc(vts), vlado), "par": par}
                an = {"texto": ""}
                try:
                    an = analizar_operacion(df, df.index.get_loc(vts), k, vlado, vprecio, par)
                    guardar_aprendizaje("LZ", par, LZ_EXP_VELAS * MIN_VELA, vts, ctx, avisar,
                                        motivo, res, an)
                except Exception as e:  # noqa: BLE001
                    log.warning("No se pudo guardar el aprendizaje: %s", e)
                if avisar:                       # las no avisadas solo se guardan
                    icono = {"GANADA": "✅", "PERDIDA": "❌", "EMPATE": "➖"}[res]
                    enviar_con_grafico(
                        f"🧠 {icono} <b>{res}</b> · RSI2 + Lorentzian · {nombre_par(par)} · "
                        f"{LZ_EXP_VELAS * MIN_VELA} min\n"
                        f"💵 Entrada {fmt(par, vprecio)} → cierre {fmt(par, cierre)}\n"
                        f"📊 Asertividad real: {txt_pct(*real_vig_lz())} (todos los pares)\n"
                        f"⏰ En horario bueno: {txt_pct(*real_horario(ARCHIVO_VIG_LZ))}"
                        + (f"\n{an['texto']}" if an.get("texto") else ""),
                        df, par=par, lado=vlado, ts=vts, precio=vprecio,
                        minutos=LZ_EXP_VELAS * MIN_VELA, k=k, cierre=cierre, res=res,
                        estrategia="RSI2 + Lorentzian")
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
    ctx = {**contexto(df, i, lado), "par": par}
    CONTEXTOS[("LZ", par, ts)] = ctx
    avisar, motivo = decidir_aviso("LZ", ts, ctx)
    abiertas[par] = (ts, lado, precio, avisar, motivo)
    if not avisar:
        log.info("Lorentzian %s no se avisa (%s, %s h): se mide en silencio.",
                 par, motivo, hora_entrada(ts))
        return
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
        f"{txt_pct(*real_vig_lz())} en total\n"
        f"• ⏰ Real en horario bueno: {txt_pct(*real_horario(ARCHIVO_VIG_LZ))} (todos los pares)\n"
        + texto_aprendizaje("LZ", ctx) + "\n"
        f"🔍 Vigilancia RSI2 + Lorentzian + kernel: solo observar, NO operar.",
        df, par=par, lado=lado, ts=ts, precio=precio, estrategia="RSI2 + Lorentzian")


# ------------------------------------------------- ESTRATEGIAS NUEVAS (RSI2 +)
def senal_sr(df, i, altos, bajos):
    """RSI2 < 10 tocando un soporte (CALL) o RSI2 > 90 tocando una resistencia (PUT).
    Los niveles son pivotes ya confirmados del último día, de un tramo anterior a la vela actual."""
    r2, atr = df["rsi2"].iat[i], df["atr"].iat[i]
    if not atr > 0 or np.isnan(r2):
        return None
    tol = SR_TOLERANCIA * atr
    desde, hasta = i - SR_VENTANA, i - PIVOTE_DER - 3
    l, h, c = df["Low"].iat[i], df["High"].iat[i], df["Close"].iat[i]
    def elegir(niveles, ref, toca):
        cand = [(x, sum(abs(y - x) <= tol for y in niveles)) for x in niveles if toca(x)]
        cand = [(x, t) for x, t in cand if t >= SR_TOQUES_MIN]          # solo niveles ya probados
        return min(cand, key=lambda xt: abs(ref - xt[0])) if cand else None

    if r2 < 10:
        niveles = [df["Low"].iat[j] for j in bajos if desde <= j <= hasta]
        e = elegir(niveles, l, lambda s: l <= s + tol and c >= s - tol)   # tocó y no cerró debajo
        if e:
            return dict(lado=1, nivel=e[0], toques=e[1])
    if r2 > 90:
        niveles = [df["High"].iat[j] for j in altos if desde <= j <= hasta]
        e = elegir(niveles, h, lambda r: h >= r - tol and c <= r + tol)   # tocó y no cerró encima
        if e:
            return dict(lado=-1, nivel=e[0], toques=e[1])
    return None


def senal_env(df, i):
    """RSI2 extremo en la vela anterior y una vela envolvente en sentido contrario."""
    if i < 1:
        return None
    o1, c1, r1 = df["Open"].iat[i - 1], df["Close"].iat[i - 1], df["rsi2"].iat[i - 1]
    o, c = df["Open"].iat[i], df["Close"].iat[i]
    if r1 < 10 and c1 < o1 and c > o and c >= o1 and o <= c1 and (c - o) > (o1 - c1):
        return dict(lado=1, rsi_prev=r1)
    if r1 > 90 and c1 > o1 and c < o and c <= o1 and o >= c1 and (o - c) > (c1 - o1):
        return dict(lado=-1, rsi_prev=r1)
    return None


def senal_div(df, i):
    """Divergencia: mínimo más bajo con RSI2 más alto (CALL) o máximo más alto con RSI2 más bajo
    (PUT). El extremo anterior está entre 5 y 40 velas atrás y entre los dos el RSI2 cruzó 50."""
    a, b = DIV_VENTANA
    if i < b + 2:
        return None
    r2, lo, hi = df["rsi2"].values, df["Low"].values, df["High"].values
    ventana = range(i - b, i - a + 1)
    if r2[i] < 10 and lo[i] <= lo[i - 2:i].min():
        cand = [k for k in ventana if r2[k] < 20]
        if cand:
            k = min(cand, key=lambda j: lo[j])
            if lo[i] < lo[k] and r2[i] > r2[k] + 2 and r2[k + 1:i].max() > 50:
                return dict(lado=1, previo=k, p_i=lo[i], p_k=lo[k], r_i=r2[i], r_k=r2[k])
    if r2[i] > 90 and hi[i] >= hi[i - 2:i].max():
        cand = [k for k in ventana if r2[k] > 80]
        if cand:
            k = max(cand, key=lambda j: hi[j])
            if hi[i] > hi[k] and r2[i] < r2[k] - 2 and r2[k + 1:i].min() < 50:
                return dict(lado=-1, previo=k, p_i=hi[i], p_k=hi[k], r_i=r2[i], r_k=r2[k])
    return None


def detalle_nueva(estr, s, par, i):
    sube = s["lado"] > 0
    if estr == "SR":
        return (f"{'Soporte' if sube else 'Resistencia'} en {fmt(par, s['nivel'])} "
                f"(nivel tocado {s['toques']} veces en el último día)")
    if estr == "ENV":
        return f"Vela envolvente {'alcista' if sube else 'bajista'} después de RSI2 {s['rsi_prev']:.0f}"
    hace = (i - s["previo"]) * MIN_VELA
    return (f"Divergencia {'alcista' if sube else 'bajista'}: "
            f"{'mínimo más bajo' if sube else 'máximo más alto'} ({fmt(par, s['p_i'])} vs "
            f"{fmt(par, s['p_k'])} hace {hace} min) pero RSI2 {s['r_i']:.0f} vs {s['r_k']:.0f}")


def guardar_nueva(estr, par, exp, lado, precio, cierre, ts):
    nuevo = not os.path.exists(ARCHIVO_NUEVAS)
    dif = (cierre - precio) * lado
    res = "EMPATE" if dif == 0 else ("GANADA" if dif > 0 else "PERDIDA")
    with open(ARCHIVO_NUEVAS, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("fecha_utc,estrategia,par,expiracion_min,direccion,entrada,cierre,resultado\n")
        f.write(f"{ts:%Y-%m-%d %H:%M},{estr},{par},{exp * MIN_VELA},{'CALL' if lado > 0 else 'PUT'},"
                f"{precio},{cierre},{res}\n")
    return res


def real_nueva(estr, exp_min, par=None):
    """Asertividad real de una estrategia nueva: (ganadas, total) sin empates."""
    if not os.path.exists(ARCHIVO_NUEVAS):
        return 0, 0
    v = pd.read_csv(ARCHIVO_NUEVAS)
    v = v[(v["estrategia"] == estr) & (v["expiracion_min"] == exp_min) & (v["resultado"] != "EMPATE")]
    if par:
        v = v[v["par"] == par]
    return int((v["resultado"] == "GANADA").sum()), len(v)


def resumen_nuevas():
    if not NUEVAS_ACTIVAS:
        return ""
    lineas = ["\n\n🆕 <b>Estrategias nuevas</b> (todo el horario, 7 pares, meta ≥56 %):"]
    v = pd.read_csv(ARCHIVO_NUEVAS) if os.path.exists(ARCHIVO_NUEVAS) else pd.DataFrame(
        columns=["fecha_utc", "estrategia", "expiracion_min", "resultado"])
    v = v[v["resultado"] != "EMPATE"]
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    for estr in NUEVAS_ACTIVAS:
        emoji, nombre = NUEVAS[estr]
        partes = []
        for e in NUEVAS_EXP_VELAS:
            x = v[(v["estrategia"] == estr) & (v["expiracion_min"] == e * MIN_VELA)]
            xh = x[pd.to_datetime(x["fecha_utc"], utc=True) >= hoy]
            gan = lambda y: int((y["resultado"] == "GANADA").sum())  # noqa: E731
            partes.append(f"{e * MIN_VELA}m hoy {gan(xh)}/{len(xh)} · total {txt_pct(gan(x), len(x))}")
        lineas.append(f"{emoji} {nombre}\n   " + " | ".join(partes))
    return "\n".join(lineas)


def vigilar_nuevas(par, df, i, ts, abiertas, vistas, altos, bajos):
    """Revisa resultados de las estrategias nuevas abiertas y busca señales nuevas, por separado."""
    for (estr, p, e), (vts, lado, precio, avisar, motivo, s) in list(abiertas.items()):
        if p != par:
            continue
        if vts not in df.index:
            del abiertas[(estr, p, e)]
            CONTEXTOS.pop((estr, par, vts), None)
            continue
        ie = df.index.get_loc(vts)
        k = ie + e
        if k > i:
            continue
        cierre = df["Close"].iat[k]
        res = guardar_nueva(estr, par, e, lado, precio, cierre, vts)
        ctx = CONTEXTOS.get((estr, par, vts)) or {**contexto(df, ie, lado), "par": par}
        an = {"texto": ""}
        try:
            an = analizar_operacion(df, ie, k, lado, precio, par, s.get("nivel"))
            guardar_aprendizaje(estr, par, e * MIN_VELA, vts, ctx, avisar, motivo, res, an)
        except Exception as ex:  # noqa: BLE001
            log.warning("No se pudo guardar el aprendizaje (%s): %s", estr, ex)
        del abiertas[(estr, p, e)]
        if not any(x == estr and y == par and t[0] == vts for (x, y, _), t in abiertas.items()):
            CONTEXTOS.pop((estr, par, vts), None)
        if avisar:                                  # las no avisadas solo se guardan
            emoji, nombre = NUEVAS[estr]
            icono = {"GANADA": "✅", "PERDIDA": "❌", "EMPATE": "➖"}[res]
            enviar_con_grafico(
                f"{emoji} {icono} <b>{res}</b> · {nombre} · {nombre_par(par)} · {e * MIN_VELA} min\n"
                f"💵 Entrada {fmt(par, precio)} → cierre {fmt(par, cierre)}\n"
                f"📊 Asertividad real: {txt_pct(*real_nueva(estr, e * MIN_VELA))} (todos los pares)"
                + (f"\n{an['texto']}" if an.get("texto") else ""),
                df, par=par, lado=lado, ts=vts, precio=precio, minutos=e * MIN_VELA, k=k,
                cierre=cierre, res=res, estrategia=nombre, nivel=s.get("nivel"))

    if (par, ts) in vistas or not en_horario(ts):
        return
    vistas.add((par, ts))
    detectores = {"SR": lambda: senal_sr(df, i, altos, bajos),
                  "ENV": lambda: senal_env(df, i),
                  "DIV": lambda: senal_div(df, i)}
    for estr in NUEVAS_ACTIVAS:
        libres = [e for e in NUEVAS_EXP_VELAS if (estr, par, e) not in abiertas]
        if not libres:
            continue
        s = detectores[estr]()
        if not s:
            continue
        lado, precio = s["lado"], df["Close"].iat[i]
        ctx = {**contexto(df, i, lado), "par": par}
        CONTEXTOS[(estr, par, ts)] = ctx
        avisos = {e: decidir_aviso(clave_de(estr, par, e * MIN_VELA), ts, ctx, usar_horario=False)
                  for e in libres}
        for e in libres:
            abiertas[(estr, par, e)] = (ts, lado, precio, *avisos[e], s)
        avisar = [e for e in libres if avisos[e][0]]
        if not avisar:
            log.info("%s %s no se avisa (condición mala aprendida): se mide en silencio.", estr, par)
            continue
        emoji, nombre = NUEVAS[estr]
        hora_col = (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
        sentido = "arriba ⬆️" if lado > 0 else "abajo ⬇️"
        pcts = [f"• {e * MIN_VELA} min: {txt_pct(*real_nueva(estr, e * MIN_VELA))} todos los pares · "
                f"{txt_pct(*real_nueva(estr, e * MIN_VELA, par))} en {nombre_par(par)}" for e in avisar]
        enviar_con_grafico(
            f"{emoji} <b>{nombre} · {nombre_par(par)} - {sentido}</b>\n"
            f"Zona horaria UTC -5\n\n"
            f"⌛ EXPIRACIÓN: {' y '.join(str(e * MIN_VELA) for e in avisar)} minutos\n"
            f"⏱️ ENTRADA: {hora_col:%H:%M}\n"
            f"💵 Precio: {fmt(par, precio)}\n"
            f"📌 {detalle_nueva(estr, s, par, i)}\n"
            f"📈 Tendencia: {ctx['tendencia']}\n\n"
            f"📊 <b>ASERTIVIDAD REAL</b>\n" + "\n".join(pcts) + "\n"
            + texto_aprendizaje(clave_de(estr, par, avisar[0] * MIN_VELA), ctx) + "\n"
            "🔍 Estrategia nueva en vigilancia: solo observar, NO operar.",
            df, par=par, lado=lado, ts=ts, precio=precio, estrategia=nombre, nivel=s.get("nivel"))


def resumen_horario():
    """Cómo va el filtro de horario: dentro vs fuera de las horas buenas (sin empates)."""
    if not HORAS_BUENAS:
        return ""
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    lineas = [f"\n\n⏰ <b>Filtro de horario</b> ({texto_horas()}, meta ≥56 %):"]
    fuentes = [("RSI2", ARCHIVO_VIGILANCIA)]
    if VIG_LZ:
        fuentes.append(("RSI2 + Lorentzian", ARCHIVO_VIG_LZ))
    for nombre, archivo in fuentes:
        if not os.path.exists(archivo):
            lineas.append(f"{nombre}: aún sin datos")
            continue
        v = pd.read_csv(archivo)
        v = v[v["resultado"] != "EMPATE"]
        dentro, fuera = separar_horario(v)
        dh = dentro[pd.to_datetime(dentro["fecha_utc"], utc=True) >= hoy]
        gan = lambda x: int((x["resultado"] == "GANADA").sum())  # noqa: E731
        lineas.append(f"{nombre} en horario bueno — hoy {txt_pct(gan(dh), len(dh))} · "
                      f"total {txt_pct(gan(dentro), len(dentro))}")
        lineas.append(f"   fuera de horario (no avisadas): total {txt_pct(gan(fuera), len(fuera))}")
    return "\n".join(lineas)


def mensaje_resumen():
    if not os.path.exists(ARCHIVO_RESULTADOS):
        return ("📋 <b>Resumen del día</b>\nHoy no hubo señales que pasaran el filtro."
                + resumen_horario() + resumen_vigilancia() + resumen_vig_lz() + resumen_nuevas()
                + resumen_aprendizaje())
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
    return ("📋 <b>Resumen del día</b>\n" + "\n".join(partes) + resumen_horario()
            + resumen_vigilancia() + resumen_vig_lz() + resumen_nuevas() + resumen_aprendizaje())


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
    vig_abiertas = {}   # (par, exp) -> (ts, lado, precio, avisar, motivo)
    vig_vistas = set()
    lz_abiertas = {}    # par -> (ts, lado, precio, avisar, motivo) de la vigilancia D
    lz_vistas = set()
    nuevas_abiertas = {}   # (estrategia, par, exp) -> (ts, lado, precio, avisar, motivo, señal)
    nuevas_vistas = set()
    actualizar_aprendizaje()

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
            actualizar_aprendizaje()

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
            for (vp, ve), (vts, vlado, vprecio, vavisar, vmotivo) in list(vig_abiertas.items()):
                if vp != par or vts not in df.index:
                    continue
                k = df.index.get_loc(vts) + ve
                if k <= i:
                    cierre = df["Close"].iat[k]
                    res = guardar_vigilancia(par, ve, vlado, vprecio, cierre, vts)
                    ctx = CONTEXTOS.get(("RSI2", par, vts)) or contexto(df, df.index.get_loc(vts), vlado)
                    an = {"texto": ""}
                    try:
                        an = analizar_operacion(df, df.index.get_loc(vts), k, vlado, vprecio, par)
                        guardar_aprendizaje("RSI2", par, ve * MIN_VELA, vts, ctx, vavisar, vmotivo,
                                            res, an)
                    except Exception as e:  # noqa: BLE001
                        log.warning("No se pudo guardar el aprendizaje: %s", e)
                    if not any(p == par and t[0] == vts for (p, _), t in vig_abiertas.items()
                               if (p, _) != (vp, ve)):
                        CONTEXTOS.pop(("RSI2", par, vts), None)
                    if vavisar:                  # las no avisadas solo se guardan
                        icono = {"GANADA": "✅", "PERDIDA": "❌", "EMPATE": "➖"}[res]
                        enviar_con_grafico(
                            f"🔍 {icono} <b>{res}</b> · {nombre_par(par)} · {ve * MIN_VELA} min\n"
                            f"💵 Entrada {fmt(par, vprecio)} → cierre {fmt(par, cierre)}\n"
                            f"📊 Asertividad real: {txt_pct(*real_vig(par, ve))}\n"
                            f"⏰ En horario bueno: "
                            f"{txt_pct(*real_horario(ARCHIVO_VIGILANCIA, par, ve * MIN_VELA))}"
                            + (f"\n{an['texto']}" if an.get("texto") else ""),
                            df, par=par, lado=vlado, ts=vts, precio=vprecio,
                            minutos=ve * MIN_VELA, k=k, cierre=cierre, res=res)
                    del vig_abiertas[(vp, ve)]
            if par in VIGILANCIA and (par, ts) not in vig_vistas and en_horario(ts):
                vig_vistas.add((par, ts))
                libres = [e for e in VIGILANCIA[par] if (par, e) not in vig_abiertas]
                lado = senal_rsi2(df, i) if libres else 0
                if lado:
                    precio = df["Close"].iat[i]
                    ctx = contexto(df, i, lado)
                    CONTEXTOS[("RSI2", par, ts)] = ctx
                    avisos = {e: decidir_aviso(clave_de("RSI2", par, e * MIN_VELA), ts, ctx)
                              for e in libres}
                    for e in libres:
                        vig_abiertas[(par, e)] = (ts, lado, precio, *avisos[e])
                    libres = [e for e in libres if avisos[e][0]]     # solo se avisan estas
                    if not libres:
                        log.info("RSI2 %s no se avisa (%s, %s h): se mide en silencio.", par,
                                 "/".join(sorted({m for _, m in avisos.values()})), hora_entrada(ts))
                if lado and libres:
                    hora_col = (ts + pd.Timedelta(minutes=MIN_VELA)).tz_convert("America/Bogota")
                    pcts = []
                    for e in libres:
                        hg, ht = TABLA_VIG.get(par, {}).get(e, (0, 0))
                        pcts.append(f"• {e * MIN_VELA} min: histórico {txt_pct(hg, ht)} · "
                                    f"real {txt_pct(*real_vig(par, e))} · ⏰ en horario bueno "
                                    f"{txt_pct(*real_horario(ARCHIVO_VIGILANCIA, par, e * MIN_VELA))}")
                    flecha = "🔺🔺" if lado > 0 else "🔻🔻"
                    sentido = "arriba ⬆️" if lado > 0 else "abajo ⬇️"
                    enviar_con_grafico(
                        f"{flecha} <b>Par de divisas: {nombre_par(par)} - {sentido}</b>\n"
                        f"Zona horaria UTC -5\n\n"
                        f"⌛ EXPIRACIÓN: {' y '.join(str(e * MIN_VELA) for e in libres)} minutos\n"
                        f"⏱️ ENTRADA: {hora_col:%H:%M}\n"
                        f"💵 Precio: {fmt(par, precio)}\n\n"
                        f"📊 <b>ASERTIVIDAD</b>\n" + "\n".join(pcts) + "\n"
                        + texto_aprendizaje(clave_de("RSI2", par, libres[0] * MIN_VELA), ctx) + "\n"
                        f"🔍 Modo vigilancia (RSI2): solo observar, NO operar.",
                        df, par=par, lado=lado, ts=ts, precio=precio)

            # ---- Vigilancia D: RSI2 + Lorentzian + kernel (todos los pares, 15 min)
            if VIG_LZ:
                try:
                    vigilar_lz(par, df, i, ts, lz_abiertas, lz_vistas)
                except Exception as e:  # noqa: BLE001
                    log.warning("Vigilancia Lorentzian %s: %s", par, e)

            # ---- Estrategias nuevas: RSI2 + S/R, RSI2 + envolvente, RSI2 + divergencia
            if NUEVAS_ACTIVAS:
                try:
                    altos_n, bajos_n = pivotes(df)
                    vigilar_nuevas(par, df, i, ts, nuevas_abiertas, nuevas_vistas, altos_n, bajos_n)
                except Exception as e:  # noqa: BLE001
                    log.warning("Estrategias nuevas %s: %s", par, e)

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
