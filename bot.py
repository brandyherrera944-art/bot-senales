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


def mensaje_resumen():
    if not os.path.exists(ARCHIVO_RESULTADOS):
        return ("📋 <b>Resumen del día</b>\nHoy no hubo señales que pasaran el filtro."
                + resumen_vigilancia())
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
    return "📋 <b>Resumen del día</b>\n" + "\n".join(partes) + resumen_vigilancia()


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


def calcular_tabla():
    datos = descargar(PARES, periodo="59d")
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
                    enviar(f"🔍 {icono} <b>{res}</b> · {nombre_par(par)} · {ve * MIN_VELA} min\n"
                           f"💵 Entrada {fmt(par, vprecio)} → cierre {fmt(par, cierre)}\n"
                           f"📊 Asertividad real: {txt_pct(*real_vig(par, ve))}")
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
                    linea = "━━━━━━━━━━━━━━"
                    pcts = []
                    for e in libres:
                        hg, ht = TABLA_VIG.get(par, {}).get(e, (0, 0))
                        pcts.append(f"• {e * MIN_VELA} min: histórico {txt_pct(hg, ht)}\n"
                                    f"   real {txt_pct(*real_vig(par, e))}")
                    enviar(f"{linea}\n🔍 <b>VIGILANCIA · {nombre_par(par)}</b>\n"
                           f"{'🟢 CALL ⬆️ (sube)' if lado > 0 else '🔴 PUT ⬇️ (baja)'}\n"
                           f"⏰ Entrada: {hora_col:%H:%M} (hora Colombia)\n"
                           f"💵 Precio: {fmt(par, precio)}\n"
                           f"🧠 Estrategia: RSI2 extremo\n{linea}\n"
                           f"📊 <b>Asertividad</b>\n" + "\n".join(pcts) + "\n"
                           f"{linea}\n⚠️ Solo observación: NO operar.")

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
