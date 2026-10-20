"""
Prueba larga de la VIGILANCIA RSI2 (la misma regla que usa bot.py, sin cambiar nada):
  CALL si el cierre está sobre la EMA200 de 5 min, la tendencia de 1 h es alcista y RSI(2) < 10.
  PUT  si el cierre está bajo la EMA200 de 5 min, la tendencia de 1 h es bajista y RSI(2) > 90.
Horario del bot (lun-vie, 7 a 20 h UTC). Una operación abierta a la vez por par y expiración.

Datos: velas de 1 min de HistData.com (mercado real, no OTC) de enero 2024 a septiembre 2026,
pasadas a 5 min. Pares en vigilancia: USD/CAD y GBP/USD.

Se mide:
  - Expiraciones 15 y 30 min (las de la vigilancia) y también 1 h, 2 h y 4 h.
  - Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
  - Por trimestre, para ver si es constante o fue suerte de una época.
  - Variante con filtro de noticias: sin entradas de 8:15 a 9:00 ni de 9:45 a 10:15 hora NY
    (cuando salen casi todos los datos fuertes de EE. UU. y Canadá).
Binarias con pago de 85 %: hace falta acertar más de 54,1 % para no perder (meta del bot: 56 %).
"""

import io
import os
import re
import sys
import time
import zipfile

import numpy as np
import pandas as pd
import requests

import bot

PARES = [p.strip().upper() for p in os.getenv("PARES_RSI2", "USDCAD,GBPUSD").split(",") if p.strip()]
PERIODOS = ["2024", "2025"] + [f"2026-{m:02d}" for m in range(1, 10)]
EXPIRACIONES = {15: "15 min", 30: "30 min", 60: "1 h", 120: "2 h", 240: "4 h"}
PAGO = 0.85
EQUILIBRIO = 100 / (1 + PAGO)          # 54,05 %


# ---------------------------------------------------------------- DATOS ----
def bajar(par, periodo):
    año, _, mes = periodo.partition("-")
    url = (f"https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/"
           f"{par.lower()}/{año}" + (f"/{int(mes)}" if mes else ""))
    for intento in range(3):
        try:
            s = requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0"
            pagina = s.get(url, timeout=30)
            campos = dict(re.findall(r'<input type="hidden" name="(\w+)" id="\w+" value="([^"]*)"',
                                     pagina.text))
            if "tk" not in campos:
                raise RuntimeError(f"sin formulario (HTTP {pagina.status_code})")
            r = s.post("https://www.histdata.com/get.php", data=campos,
                       headers={"Referer": url}, timeout=300)
            if r.status_code != 200 or not r.content.startswith(b"PK"):
                raise RuntimeError(f"descarga falló (HTTP {r.status_code})")
            z = zipfile.ZipFile(io.BytesIO(r.content))
            nombre = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            df = pd.read_csv(z.open(nombre), sep=";", header=None,
                             names=["dt", "open", "high", "low", "close", "vol"])
            t = pd.to_datetime(df["dt"], format="%Y%m%d %H%M%S") + pd.Timedelta(hours=5)  # EST -> UTC
            df.index = pd.DatetimeIndex(t).tz_localize("UTC")
            print(f"{par} {periodo}: {len(df)} velas de 1 min", flush=True)
            return df[["open", "high", "low", "close"]]
        except Exception as e:  # noqa: BLE001
            print(f"{par} {periodo}: intento {intento + 1} falló: {e}", flush=True)
            time.sleep(5)
    return None


def velas_5m(par):
    partes = [p for p in (bajar(par, x) for x in PERIODOS) if p is not None]
    if not partes:
        return None
    m1 = pd.concat(partes).sort_index()
    m1 = m1[~m1.index.duplicated()]
    g = m1.resample("5min", label="left", closed="left")
    df = pd.DataFrame({"Open": g["open"].first(), "High": g["high"].max(),
                       "Low": g["low"].min(), "Close": g["close"].last()}).dropna()
    return df[df["High"] > df["Low"]]          # quita velas planas (mercado cerrado)


# ------------------------------------------------------------ OPERACIONES ---
def hora_noticias(ts_cierre):
    t = ts_cierre.tz_convert("America/New_York")
    m = t.hour * 60 + t.minute
    return (8 * 60 + 15 <= m <= 9 * 60) or (9 * 60 + 45 <= m <= 10 * 60 + 15)


def operaciones(par, df):
    """Todas las señales RSI2 del bot, con el resultado para cada expiración."""
    df = bot.indicadores(df)
    c = df["Close"]
    lado = np.where((c > df["ema_l"]) & (df["tend_1h"] > 0) & (df["rsi2"] < 10), 1,
                    np.where((c < df["ema_l"]) & (df["tend_1h"] < 0) & (df["rsi2"] > 90), -1, 0))
    cierre_vela = df.index + pd.Timedelta(minutes=bot.MIN_VELA)
    precio_en = pd.Series(c.values, index=cierre_vela)       # precio al cierre de cada vela
    filas = []
    ocupado_hasta = {e: pd.Timestamp(0, tz="UTC") for e in EXPIRACIONES}
    for i in np.flatnonzero(lado):
        if i < bot.EMA_LENTA + 10 or not bot.en_horario(df.index[i]):
            continue
        entrada_t = cierre_vela[i]
        fila = {"par": par, "fecha": entrada_t, "lado": "CALL" if lado[i] > 0 else "PUT",
                "entrada": c.iat[i], "noticias": hora_noticias(entrada_t)}
        for e in EXPIRACIONES:
            fin = entrada_t + pd.Timedelta(minutes=e)
            if entrada_t < ocupado_hasta[e] or fin not in precio_en.index:
                fila[f"r_{e}"] = np.nan                       # ocupado o sin precio exacto al vencer
                continue
            ocupado_hasta[e] = fin
            dif = (precio_en[fin] - c.iat[i]) * lado[i]
            fila[f"r_{e}"] = np.nan if dif == 0 else float(dif > 0)   # empate = se devuelve
        filas.append(fila)
    return pd.DataFrame(filas)


# ------------------------------------------------------------- RESUMEN -----
def pct(x):
    x = x.dropna()
    if len(x) == 0:
        return "sin ops", None
    p = 100 * x.mean()
    margen = 196 * np.sqrt(x.mean() * (1 - x.mean()) / len(x))       # 95 % de confianza
    ganancia = len(x) * (x.mean() * PAGO - (1 - x.mean()))           # en montos de 1 operación
    return f"{p:.1f}% de {len(x)} (±{margen:.1f}) · {ganancia:+.0f}", p


def main():
    todas = []
    rango = None
    for par in PARES:
        df = velas_5m(par)
        if df is None or len(df) < 1000:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        print(f"{par}: {len(df)} velas de 5 min {df.index[0]} -> {df.index[-1]}", flush=True)
        rango = (df.index[0], df.index[-1])
        todas.append(operaciones(par, df))
    if not todas:
        bot.enviar("⚠️ Prueba RSI2: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.concat(todas, ignore_index=True)
    ops.to_csv("operaciones_rsi2.csv", index=False)
    corte = rango[0] + (rango[1] - rango[0]) * 2 / 3

    lineas = [
        "🔬 <b>Vigilancia RSI2 · prueba con ~3 años de datos reales</b>",
        f"HistData {rango[0]:%Y-%m-%d} a {rango[1]:%Y-%m-%d} · misma regla y horario del bot.",
        f"Con pago 85 % no pierdes si aciertas más de {EQUILIBRIO:.1f} % (meta del bot 56 %).",
        "Formato: acierto de N ops (± margen) · ganancia en montos de 1 operación.",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
    ]
    vigilados = {("USDCAD", 15), ("USDCAD", 30), ("GBPUSD", 30)}
    aprobados = []
    for par in PARES:
        x = ops[ops["par"] == par]
        if x.empty:
            continue
        lineas.append(f"\n<b>{bot.nombre_par(par)}</b>")
        for e, nombre in EXPIRACIONES.items():
            col = f"r_{e}"
            marca = " 🔍" if (par, e) in vigilados else ""
            tot, p_tot = pct(x[col])
            ent, p_ent = pct(x.loc[x["fecha"] < corte, col])
            pru, p_pru = pct(x.loc[x["fecha"] >= corte, col])
            lineas.append(f"• {nombre}{marca}: {tot}\n   entreno {ent}\n   prueba {pru}")
            if p_ent and p_pru and p_ent >= 56 and p_pru >= 56:
                aprobados.append(f"{bot.nombre_par(par)} {nombre}")

    lineas.append("\n<b>Con filtro de noticias</b> (sin 8:15-9:00 ni 9:45-10:15 NY)")
    sin_not = ops[~ops["noticias"]]
    for par, e in sorted(vigilados):
        x = sin_not[sin_not["par"] == par]
        lineas.append(f"• {bot.nombre_par(par)} {EXPIRACIONES[e]}: {pct(x[f'r_{e}'])[0]}")

    lineas.append("\n<b>Por trimestre</b> (acierto, vigilancia actual)")
    ops["tri"] = pd.to_datetime(ops["fecha"], utc=True).dt.tz_localize(None).dt.to_period("Q")
    for par, e in sorted(vigilados):
        x = ops[ops["par"] == par].dropna(subset=[f"r_{e}"])
        t = x.groupby("tri")[f"r_{e}"].agg(["mean", "count"])
        arriba = int((t["mean"] * 100 > EQUILIBRIO).sum())
        lineas.append(f"• {bot.nombre_par(par)} {EXPIRACIONES[e]}: {arriba} de {len(t)} trimestres "
                      f"sobre {EQUILIBRIO:.0f} % · " +
                      " ".join(f"{100 * m:.0f}" for m, _ in t.itertuples(index=False)))

    if aprobados:
        lineas.append("\n✅ Superan 56 % en entreno Y en prueba: " + ", ".join(aprobados))
    else:
        lineas.append("\n❌ Ninguna combinación supera 56 % en entreno y en prueba a la vez.")
    lineas.append("Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
