"""
Descarga velas de 1 minuto de EUR/USD directamente del datafeed público de Dukascopy (banco suizo,
mercado real) y las convierte a velas de 5 minutos en datos/eurusd_m5.csv.
Uso: python descargar_dukascopy.py 2024-10-01 2026-10-17
"""

import lzma
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

URL = "https://datafeed.dukascopy.com/datafeed/EURUSD/{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
TIPO = np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])
ESCALA = 1e5


def decodificar(dia, contenido):
    if not contenido:
        return None
    a = np.frombuffer(lzma.decompress(contenido), dtype=TIPO)
    if len(a) == 0:
        return None
    t = pd.Timestamp(dia, tz="UTC") + pd.to_timedelta(a["t"].astype("int64"), unit="s")
    df = pd.DataFrame({"Open": a["o"] / ESCALA, "High": a["h"] / ESCALA, "Low": a["l"] / ESCALA,
                       "Close": a["c"] / ESCALA, "Vol": a["v"]}, index=t)
    return df[df["Vol"] > 0]


def bajar_dia(dia):
    url = URL.format(y=dia.year, m=dia.month - 1, d=dia.day)   # Dukascopy cuenta meses desde 0
    estado = None
    for intento in range(5):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            estado = r.status_code
            if r.status_code == 200:
                return dia, decodificar(dia, r.content), 200
            if r.status_code == 404:
                return dia, None, 404
        except Exception as e:  # noqa: BLE001
            estado = type(e).__name__
        time.sleep(2 * (intento + 1))
    return dia, None, estado


def main():
    desde, hasta = pd.Timestamp(sys.argv[1]), pd.Timestamp(sys.argv[2])
    dias = [d for d in pd.date_range(desde, hasta, freq="D") if d.weekday() != 5]   # sábados sin mercado
    partes, estados = [], {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for dia, df, est in ex.map(bajar_dia, dias):
            estados[est] = estados.get(est, 0) + 1
            if df is not None and len(df):
                partes.append(df)
    print("Días pedidos:", len(dias), "· respuestas:", estados, "· días con datos:", len(partes))
    if not partes:
        raise SystemExit("No se pudo descargar ningún día de Dukascopy.")
    m1 = pd.concat(partes).sort_index()
    g = m1.resample("5min", label="left", closed="left")
    m5 = pd.DataFrame({"open": g["Open"].first(), "high": g["High"].max(),
                       "low": g["Low"].min(), "close": g["Close"].last()}).dropna()
    m5.insert(0, "timestamp",
              ((m5.index - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)))
    m5.to_csv("datos/eurusd_m5.csv", index=False)
    print("Velas de 5 min guardadas:", len(m5), m5.index[0], "->", m5.index[-1])


if __name__ == "__main__":
    main()
