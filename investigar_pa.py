"""
Prueba de ACCIÓN DEL PRECIO: el patrón se busca en velas de 5 min y la entrada en velas de 1 min.
Reglas fijadas ANTES de ver los datos (no se ajustan después de ver resultados):

A) Rechazo en soporte/resistencia
   Nivel = último pivote de 5 min (3 velas a cada lado) de las últimas 4 h, si hay otro pivote
   del mismo tipo en esas 4 h a menos de 0,3 ATR (el nivel se tocó 2 o más veces) y ninguna
   vela cerró pasándolo después. Una vela de 5 min toca el nivel, cierra del lado de adentro y su
   mecha hacia el nivel es al menos la mitad de la vela. Entrada: al cierre de la siguiente vela
   de 1 min, solo si esa vela cierra a favor del rechazo.
B) Ruptura y retesteo
   Una vela de 5 min con cuerpo de al menos la mitad de la vela es la primera en cerrar pasando
   el último pivote de las últimas 4 h por más de 0,1 ATR. En los 30 min siguientes, la primera
   vela de 1 min que vuelve a tocar el nivel y cierra a favor de la ruptura da la entrada.
   Si antes una vela de 1 min cierra del otro lado del nivel, la ruptura se cancela.
C) Envolvente de 1 min a favor de la tendencia de 5 min
   Tendencia alcista en 5 min = los 2 últimos máximos pivote y los 2 últimos mínimos pivote suben
   (bajista: bajan). Retroceso = la vela de 1 min anterior va en contra y cierra peor que 5 min
   antes. Envolvente = vela a favor cuyo cuerpo cubre el cuerpo anterior. Entrada al cierre de la
   envolvente, si el precio no tocó el último mínimo pivote (máximo, si es bajista).

Expiraciones 1, 2, 3 y 5 min. Horario del bot (lun-vie, 7 a 20 h UTC).
Una operación abierta a la vez por par, patrón y expiración. Empates no cuentan.
Datos: velas de 1 min de HistData.com (enero 2024 a septiembre 2026), los 7 pares del bot.
Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
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

PARES = [p.strip().upper() for p in os.getenv("PARES_PA", ",".join(bot.PARES)).split(",") if p.strip()]
PERIODOS = ["2024", "2025"] + [f"2026-{m:02d}" for m in range(1, 10)]
EXPIRACIONES = [1, 2, 3, 5]
PAGO = 0.85
EQUILIBRIO = 100 / (1 + PAGO)          # 54,05 %
META = 56.0
MIN_OPS_PRUEBA_PAR = 100
PATRONES = {"A": "A) Rechazo en S/R", "B": "B) Ruptura y retesteo", "C": "C) Envolvente a favor"}

PIV = 3                                # velas de 5 min a cada lado para marcar un pivote
MIN = 60 * 10**9                       # 1 minuto en nanosegundos
VENTANA = 4 * 60 * MIN                 # 4 horas
RETESTEO = 30 * MIN                    # tiempo máximo para el retesteo


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


def velas_1m(par):
    partes = [p for p in (bajar(par, x) for x in PERIODOS) if p is not None]
    if not partes:
        return None
    m1 = pd.concat(partes).sort_index()
    return m1[~m1.index.duplicated()]


def a_5m(m1):
    g = m1.resample("5min", label="left", closed="left")
    df = pd.DataFrame({"o": g["open"].first(), "h": g["high"].max(),
                       "l": g["low"].min(), "c": g["close"].last()}).dropna()
    return df[df["h"] > df["l"]]                     # quita velas planas (mercado cerrado)


def preparar(m1):
    """Arreglos de 1 min y de 5 min, ATR(14) y pivotes de 5 min."""
    d5 = a_5m(m1)
    pc = d5["c"].shift(1)
    tr = pd.concat([d5["h"] - d5["l"], (d5["h"] - pc).abs(), (d5["l"] - pc).abs()], axis=1).max(axis=1)
    w = 2 * PIV + 1
    ph = np.flatnonzero((d5["h"].rolling(w, center=True).max() == d5["h"]).values)
    pl = np.flatnonzero((d5["l"].rolling(w, center=True).min() == d5["l"]).values)
    i1 = m1.index
    return dict(
        T1=i1.as_unit("ns").asi8, O1=m1["open"].values, H1=m1["high"].values, L1=m1["low"].values,
        C1=m1["close"].values,
        horario=((i1.dayofweek < 5) & (i1.hour >= bot.HORA_INICIO_UTC)
                 & (i1.hour < bot.HORA_FIN_UTC)),    # igual que bot.en_horario
        T5=d5.index.as_unit("ns").asi8, O5=d5["o"].values, H5=d5["h"].values, L5=d5["l"].values,
        C5=d5["c"].values, A5=bot.rma(tr, 14).values,
        ph=ph, pl=pl)            # un pivote en j recién se conoce al cierre de la vela j + PIV


def pivotes_recientes(piv, T5, k):
    """Pivotes ya confirmados antes de la vela k y de las últimas 4 h."""
    desde = np.searchsorted(T5, T5[k] - VENTANA, "left")
    a = np.searchsorted(piv, desde, "left")
    b = np.searchsorted(piv, k - PIV - 1, "right")
    return piv[a:b]


# ------------------------------------------------------------- PATRONES ----
def rechazo(d):
    T1, O1, C1 = d["T1"], d["O1"], d["C1"]
    T5, O5, H5, L5, C5, A5 = d["T5"], d["O5"], d["H5"], d["L5"], d["C5"], d["A5"]
    senales = []
    for k in range(60, len(T5)):
        a, rango = A5[k - 1], H5[k] - L5[k]
        if not a > 0 or rango <= 0:
            continue
        lados = []
        s = pivotes_recientes(d["ph"], T5, k)               # resistencia -> PUT
        if len(s) >= 2:
            nivel = H5[s[-1]]
            if (np.any(np.abs(H5[s[:-1]] - nivel) <= 0.3 * a) and C5[s[-1] + 1:k].max() <= nivel
                    and H5[k] >= nivel - 0.1 * a and C5[k] < nivel
                    and H5[k] - max(O5[k], C5[k]) >= 0.5 * rango):
                lados.append(-1)
        s = pivotes_recientes(d["pl"], T5, k)               # soporte -> CALL
        if len(s) >= 2:
            nivel = L5[s[-1]]
            if (np.any(np.abs(L5[s[:-1]] - nivel) <= 0.3 * a) and C5[s[-1] + 1:k].min() >= nivel
                    and L5[k] <= nivel + 0.1 * a and C5[k] > nivel
                    and min(O5[k], C5[k]) - L5[k] >= 0.5 * rango):
                lados.append(1)
        if len(lados) != 1:
            continue
        lado = lados[0]
        objetivo = T5[k] + 5 * MIN                          # primera vela de 1 min tras el cierre
        i = np.searchsorted(T1, objetivo, "left")
        if i < len(T1) and T1[i] == objetivo and (C1[i] - O1[i]) * lado > 0:
            senales.append((i, lado))
    return senales


def ruptura(d):
    T1, O1, H1, L1, C1 = d["T1"], d["O1"], d["H1"], d["L1"], d["C1"]
    T5, O5, H5, L5, C5, A5 = d["T5"], d["O5"], d["H5"], d["L5"], d["C5"], d["A5"]
    senales = []
    for k in range(60, len(T5)):
        a, rango, cuerpo = A5[k - 1], H5[k] - L5[k], abs(C5[k] - O5[k])
        if not a > 0 or rango <= 0 or cuerpo < 0.5 * rango:
            continue
        if C5[k] > O5[k]:
            s = pivotes_recientes(d["ph"], T5, k)
            if not len(s):
                continue
            nivel, lado = H5[s[-1]], 1
            if not (C5[k] > nivel + 0.1 * a and C5[s[-1] + 1:k].max() <= nivel):
                continue
        else:
            s = pivotes_recientes(d["pl"], T5, k)
            if not len(s):
                continue
            nivel, lado = L5[s[-1]], -1
            if not (C5[k] < nivel - 0.1 * a and C5[s[-1] + 1:k].min() >= nivel):
                continue
        ini = T5[k] + 5 * MIN
        i0 = np.searchsorted(T1, ini, "left")
        i1 = np.searchsorted(T1, ini + RETESTEO, "left")
        for i in range(i0, i1):
            if (C1[i] - nivel) * lado < 0:                   # cerró del otro lado: ruptura fallida
                break
            toca = L1[i] <= nivel + 0.1 * a if lado > 0 else H1[i] >= nivel - 0.1 * a
            if toca and (C1[i] - O1[i]) * lado > 0:
                senales.append((i, lado))
                break
    return senales


def ultimos_pivotes(piv, vals, n5):
    """Para cada vela de 5 min: valor del último y del penúltimo pivote ya confirmados."""
    cuantos = np.searchsorted(piv + PIV, np.arange(n5), "right")
    ult, pen = np.full(n5, np.nan), np.full(n5, np.nan)
    m = cuantos >= 1
    ult[m] = vals[piv[cuantos[m] - 1]]
    m = cuantos >= 2
    pen[m] = vals[piv[cuantos[m] - 2]]
    return ult, pen


def envolvente(d):
    T1, O, H, L, C = d["T1"], d["O1"], d["H1"], d["L1"], d["C1"]
    T5, H5, L5 = d["T5"], d["H5"], d["L5"]
    n5 = len(T5)
    alto, alto_ant = ultimos_pivotes(d["ph"], H5, n5)
    bajo, bajo_ant = ultimos_pivotes(d["pl"], L5, n5)
    sube5 = (alto > alto_ant) & (bajo > bajo_ant)
    baja5 = (alto < alto_ant) & (bajo < bajo_ant)

    i = np.arange(6, len(T1))
    p = i - 1
    k = np.searchsorted(T5 + 5 * MIN, T1[i] + MIN, "right") - 1   # última vela de 5 min cerrada
    ok = (k >= 0) & (T1[i] - T1[p] == MIN) & (T1[p] - T1[p - 5] == 5 * MIN)
    k = np.where(ok, k, 0)
    mayor = np.abs(C[i] - O[i]) > np.abs(C[p] - O[p])
    alc = (ok & mayor & (C[p] < O[p]) & (C[i] > O[i]) & (O[i] <= C[p]) & (C[i] >= O[p])
           & (C[p] < C[p - 5]) & sube5[k] & (np.minimum(L[i], L[p]) > bajo[k]))
    baj = (ok & mayor & (C[p] > O[p]) & (C[i] < O[i]) & (O[i] >= C[p]) & (C[i] <= O[p])
           & (C[p] > C[p - 5]) & baja5[k] & (np.maximum(H[i], H[p]) < alto[k]))
    return [(int(x), 1) for x in i[alc]] + [(int(x), -1) for x in i[baj]]


# ---------------------------------------------------------- OPERACIONES ----
def operaciones(par, patron, senales, d):
    T1, C1, horario = d["T1"], d["C1"], d["horario"]
    senales = sorted(s for s in senales if horario[s[0]])
    partes = []
    for e in EXPIRACIONES:
        libre, t, g = -1, [], []
        for i, lado in senales:
            if i < libre or i + e >= len(T1) or T1[i + e] - T1[i] != e * MIN:
                continue
            libre = i + e
            dif = (C1[i + e] - C1[i]) * lado
            if dif == 0:                                     # empate = se devuelve
                continue
            t.append(T1[i])
            g.append(dif > 0)
        partes.append(pd.DataFrame({"par": par, "patron": patron, "exp": e,
                                    "fecha": pd.to_datetime(np.array(t, dtype="int64"), utc=True),
                                    "gano": np.array(g, dtype=float)}))
    return pd.concat(partes, ignore_index=True)


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
    todas, inicio, fin = [], None, None
    for par in PARES:
        m1 = velas_1m(par)
        if m1 is None or len(m1) < 10000:
            print(f"{par}: sin datos suficientes", flush=True)
            continue
        inicio = m1.index[0] if inicio is None else min(inicio, m1.index[0])
        fin = m1.index[-1] if fin is None else max(fin, m1.index[-1])
        d = preparar(m1)
        del m1
        for clave, buscar in (("A", rechazo), ("B", ruptura), ("C", envolvente)):
            t0 = time.time()
            s = buscar(d)
            ops = operaciones(par, clave, s, d)
            todas.append(ops)
            print(f"{par} {PATRONES[clave]}: {len(s)} señales, {len(ops)} ops "
                  f"({time.time() - t0:.0f} s)", flush=True)
    if not todas:
        bot.enviar("⚠️ Prueba de acción del precio: no se pudieron descargar datos de HistData.")
        return 1
    ops = pd.concat(todas, ignore_index=True)
    if ops.empty:
        bot.enviar("⚠️ Prueba de acción del precio: ningún patrón dio operaciones.")
        return 1
    corte = inicio + (fin - inicio) * 2 / 3
    ops["entreno"] = ops["fecha"] < corte

    filas = []
    for (par, pat, e), x in ops.groupby(["par", "patron", "exp"]):
        ent, pru = x.loc[x["entreno"], "gano"], x.loc[~x["entreno"], "gano"]
        filas.append(dict(par=par, patron=PATRONES[pat], expiracion_min=e,
                          ops_entreno=len(ent), acierto_entreno=round(100 * ent.mean(), 1) if len(ent) else None,
                          ops_prueba=len(pru), acierto_prueba=round(100 * pru.mean(), 1) if len(pru) else None))
    r = pd.DataFrame(filas)
    r.to_csv("investigacion_pa.csv", index=False)

    lineas = [
        "🔬 <b>Acción del precio · patrón en 5 min, entrada en 1 min</b>",
        f"HistData {inicio:%Y-%m-%d} a {fin:%Y-%m-%d} · {ops['par'].nunique()} pares · horario del bot.",
        f"Con pago 85 % no pierdes si aciertas más de {EQUILIBRIO:.1f} % (meta del bot 56 %).",
        "Formato: acierto de N ops (± margen) · ganancia en montos de 1 operación.",
        f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después (días no vistos).",
    ]
    mejor = None
    for pat, nombre in PATRONES.items():
        lineas.append(f"\n<b>{nombre}</b> (todos los pares)")
        for e in EXPIRACIONES:
            x = ops[(ops["patron"] == pat) & (ops["exp"] == e)]
            tot, _ = pct(x["gano"])
            ent, _ = pct(x.loc[x["entreno"], "gano"])
            pru, p_pru = pct(x.loc[~x["entreno"], "gano"])
            lineas.append(f"• {e} min: {tot}\n   entreno {ent}\n   prueba {pru}")
            if p_pru is not None and (mejor is None or p_pru > mejor[0]):
                mejor = (p_pru, pat, e)

    cand = r[(r["acierto_entreno"] >= META) & (r["acierto_prueba"] >= META)
             & (r["ops_prueba"] >= MIN_OPS_PRUEBA_PAR)].sort_values("acierto_prueba", ascending=False)
    lineas.append(f"\n<b>Par + patrón que superan {META:.0f} % en entreno Y en prueba</b> "
                  f"(≥{MIN_OPS_PRUEBA_PAR} ops en prueba):")
    if cand.empty:
        lineas.append("❌ Ninguno.")
    for _, x in cand.head(8).iterrows():
        lineas.append(f"✅ {bot.nombre_par(x['par'])} {x['patron']} · {x['expiracion_min']} min: "
                      f"entreno {x['acierto_entreno']}% ({x['ops_entreno']}) · "
                      f"prueba {x['acierto_prueba']}% ({x['ops_prueba']})")

    if mejor:
        _, pat, e = mejor
        x = ops[(ops["patron"] == pat) & (ops["exp"] == e)].copy()
        x["tri"] = x["fecha"].dt.tz_localize(None).dt.to_period("Q")
        t = x.groupby("tri")["gano"].mean() * 100
        arriba = int((t > EQUILIBRIO).sum())
        lineas.append(f"\n<b>Por trimestre</b> (la mejor en prueba: {PATRONES[pat]} · {e} min)")
        lineas.append(f"{arriba} de {len(t)} trimestres sobre {EQUILIBRIO:.0f} % · "
                      + " ".join(f"{v:.0f}" for v in t.values))

    lineas.append(f"\nSe probaron {len(r)} combinaciones de par, patrón y expiración: "
                  "algunas pueden pasar por pura suerte.")
    lineas.append("Análisis educativo, no es asesoría financiera.")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
