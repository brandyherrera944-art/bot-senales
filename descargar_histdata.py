"""
Descarga velas de 1 minuto de EUR/USD (mercado real) de HistData.com y las convierte a 5 minutos
en datos/eurusd_m5.csv.  HistData publica la hora en EST fija (UTC-5, sin horario de verano).
Uso: python descargar_histdata.py 2024 2025 2026-01 2026-02 ...   (año completo o año-mes)
"""

import io
import re
import sys
import zipfile

import pandas as pd
import requests

BASE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/eurusd/"


def bajar(periodo):
    año, _, mes = periodo.partition("-")
    url = BASE + año + (f"/{int(mes)}" if mes else "")
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    pagina = s.get(url, timeout=30)
    campos = dict(re.findall(r'<input type="hidden" name="(\w+)" id="\w+" value="([^"]*)"', pagina.text))
    if "tk" not in campos:
        print(f"{periodo}: no se encontró el formulario (HTTP {pagina.status_code})", flush=True)
        return None
    r = s.post("https://www.histdata.com/get.php", data=campos, headers={"Referer": url}, timeout=180)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        print(f"{periodo}: descarga falló (HTTP {r.status_code}) {r.content[:120]!r}", flush=True)
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    nombre = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    df = pd.read_csv(z.open(nombre), sep=";", header=None,
                     names=["dt", "open", "high", "low", "close", "vol"])
    t = pd.to_datetime(df["dt"], format="%Y%m%d %H%M%S") + pd.Timedelta(hours=5)   # EST -> UTC
    df.index = t.dt.tz_localize("UTC")
    print(f"{periodo}: {len(df)} velas de 1 min ({df.index[0]} -> {df.index[-1]})", flush=True)
    return df[["open", "high", "low", "close"]]


def main():
    partes = [p for p in (bajar(x) for x in sys.argv[1:]) if p is not None]
    if not partes:
        raise SystemExit("No se pudo descargar nada de HistData.")
    m1 = pd.concat(partes).sort_index()
    m1 = m1[~m1.index.duplicated()]
    g = m1.resample("5min", label="left", closed="left")
    m5 = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                       "low": g["low"].min(), "close": g["close"].last()}).dropna()
    m5.insert(0, "timestamp",
              (m5.index - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1))
    m5.to_csv("datos/eurusd_m5.csv", index=False)
    print("Velas de 5 min guardadas:", len(m5), m5.index[0], "->", m5.index[-1])


if __name__ == "__main__":
    main()
