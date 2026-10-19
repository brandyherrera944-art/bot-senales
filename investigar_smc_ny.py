"""
Estrategia del video, SOLO en EUR/USD y SOLO al abrir Nueva York (entrada de 8:00 a 8:30 a. m. NY).
Datos: velas de 1 min de EUR/USD de HistData.com (mercado real, no OTC) pasadas a 5 min.

Se prueban dos versiones (reglas fijadas antes de ver datos; versión bajista, la alcista es espejo):

COMPLETA (como la explica el video):
  1) 1 HORA: confirmación = vela de impulso (cuerpo >= 1.2 ATR) que rompe la estructura
     (cierra bajo el mínimo de 20 velas) y deja un VACÍO; ORDER BLOCK = última vela contraria.
  2) 15 MIN: el precio vuelve a testear el vacío de 1 h (hasta 72 h) y, después de ese test,
     aparece una NUEVA confirmación en 15 min (quiebre + vacío en la misma dirección, en <= 24 h).
     Si el precio cierra más allá del order block de 1 h, se anula.
  3) 5 MIN: el precio testea el vacío de 15 min (hasta 12 h) y una vela de 5 min rompe la mini
     estructura (cierra más allá de las 6 velas anteriores) = ENTRADA (de 8:00 a 8:30 h NY).
  4) STOP: más allá del último nivel de estructura (extremo hecho dentro de la zona).
     TAKE PROFIT: en el punto de estructura (extremo del impulso de 15 min). También se mide 1:2.

SIMPLE (15 min -> 5 min, sin el filtro de 1 hora), para comparar.

Se cobra ~1 pip de spread por operación y se muestra también el resultado sin spread.
Binarias: expiración 15, 30 y 60 min. Entreno = primeros 2/3 del tiempo · Prueba = último 1/3.
"""

import glob
import sys

import numpy as np
import pandas as pd

import bot
import investigar_smc as smc

PIP = 0.0001
ESPERA_TEST_1H = pd.Timedelta(hours=72)
ESPERA_CONF_15 = pd.Timedelta(hours=24)


VENTANA_NY = (8 * 60, 8 * 60 + 30)   # entrada (cierre de la vela de 5 min) entre 8:00 y 8:30 NY


def en_mañana_ny(ts):
    t = (ts + pd.Timedelta(minutes=5)).tz_convert("America/New_York")   # hora de cierre de la vela
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and VENTANA_NY[0] <= m <= VENTANA_NY[1]


def cargar():
    archivos = sorted(glob.glob("datos/*.csv"))
    if not archivos:
        raise SystemExit("No se encontró el archivo de datos en datos/")
    df = pd.read_csv(archivos[0])
    df.columns = [c.strip().lower() for c in df.columns]
    df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df = df[df["High"] > df["Low"]]          # quita velas planas (mercado cerrado)
    return df[~df.index.duplicated()].sort_index()


def velas(df, regla):
    g = df.resample(regla, label="left", closed="left")
    v = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                      "Low": g["Low"].min(), "Close": g["Close"].last()}).dropna()
    pc = v["Close"].shift(1)
    tr = pd.concat([v["High"] - v["Low"], (v["High"] - pc).abs(), (v["Low"] - pc).abs()],
                   axis=1).max(axis=1)
    v["atr"] = bot.rma(tr, 14)
    return v


def confirmaciones(v, dur):
    """Quiebre + vacío + order block en el marco v (dur = duración de cada vela)."""
    o, h, l, c, atr = (v[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    out = []
    for j in range(21, len(v) - 1):
        if np.isnan(atr[j - 1]) or abs(c[j] - o[j]) < 1.2 * atr[j - 1]:
            continue
        base = dict(inicio=v.index[j], conocido=v.index[j + 1] + dur, atr=atr[j])
        if c[j] < o[j] and c[j] < l[j - 20:j].min() and h[j + 1] < l[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] > o[k]]
            out.append(dict(base, lado=-1, ob=h[obs[0]] if obs else h[j - 1],
                            borde=h[j + 1], extremo=min(l[j], l[j + 1])))
        elif c[j] > o[j] and c[j] > h[j - 20:j].max() and l[j + 1] > h[j - 1]:
            obs = [k for k in range(j - 1, j - 6, -1) if c[k] < o[k]]
            out.append(dict(base, lado=1, ob=l[obs[0]] if obs else l[j - 1],
                            borde=l[j + 1], extremo=max(h[j], h[j + 1])))
    return out


def resultado(h, l, c, m, lado, entrada, stop, objetivo):
    riesgo = abs(entrada - stop)
    for k in range(m + 1, min(m + 1 + smc.MAX_DURACION, len(c))):
        if (lado < 0 and h[k] >= stop) or (lado > 0 and l[k] <= stop):
            return -1.0
        if (lado < 0 and l[k] <= objetivo) or (lado > 0 and h[k] >= objetivo):
            return abs(objetivo - entrada) / riesgo
    k = min(m + smc.MAX_DURACION, len(c) - 1)
    return (c[k] - entrada) * lado / riesgo


def confirmaciones_amplias(v, dur):
    """CUALQUIERA de las tres confirmaciones (versión intermedia). Para cada una:
    borde = precio donde empieza la zona al volver, ob = nivel que la anula, extremo = punto de estructura.
      QUIEBRE: vela (cuerpo >= 0.5 ATR) que cierra más allá del extremo de 20 velas; zona = su order block.
      VACÍO:   hueco entre la vela j-1 y j+1 de al menos 0.1 ATR.
      ORDER BLOCK: vela contraria seguida de una vela fuerte (>= 1.2 ATR) que cierra más allá de ella."""
    o, h, l, c, atr = (v[k].values for k in ("Open", "High", "Low", "Close", "atr"))
    out = []
    for j in range(21, len(v) - 1):
        a = atr[j - 1]
        if np.isnan(a):
            continue
        cuerpo = abs(c[j] - o[j])
        for lado in (-1, 1):
            baja = lado < 0
            if (c[j] < o[j]) != baja:
                continue
            # QUIEBRE
            if cuerpo >= 0.5 * a and ((baja and c[j] < l[j - 20:j].min()) or
                                      (not baja and c[j] > h[j - 20:j].max())):
                obs = [k for k in range(j - 1, j - 6, -1) if (c[k] > o[k]) == baja]
                if obs:
                    k = obs[0]
                    out.append(dict(tipo="quiebre", lado=lado, inicio=v.index[j],
                                    conocido=v.index[j] + dur,
                                    borde=l[k] if baja else h[k], ob=h[k] if baja else l[k],
                                    extremo=l[j] if baja else h[j]))
            # VACÍO
            hueco = (l[j - 1] - h[j + 1]) if baja else (l[j + 1] - h[j - 1])
            if hueco >= 0.1 * a:
                out.append(dict(tipo="vacio", lado=lado, inicio=v.index[j],
                                conocido=v.index[j + 1] + dur,
                                borde=h[j + 1] if baja else l[j + 1],
                                ob=l[j - 1] if baja else h[j - 1],
                                extremo=min(l[j], l[j + 1]) if baja else max(h[j], h[j + 1])))
            # ORDER BLOCK
            k = j - 1
            if cuerpo >= 1.2 * a and (c[k] > o[k]) == baja and \
                    ((baja and c[j] < l[k]) or (not baja and c[j] > h[k])):
                out.append(dict(tipo="order_block", lado=lado, inicio=v.index[j],
                                conocido=v.index[j] + dur,
                                borde=l[k] if baja else h[k], ob=h[k] if baja else l[k],
                                extremo=l[j] if baja else h[j]))
    out.sort(key=lambda x: x["conocido"])
    return out


def completa(df, conf=None, flexible=False):
    conf = conf or confirmaciones
    d = bot.indicadores(df)
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    atr5, idx = d["atr"].values, d.index
    v15 = velas(df, "15min")
    c15, h15, l15 = v15["Close"].values, v15["High"].values, v15["Low"].values
    s1h = conf(velas(df, "1h"), pd.Timedelta(hours=1))
    s15 = conf(v15, pd.Timedelta(minutes=15))
    s15_ini = np.array([s["inicio"].value for s in s15])
    ops, usadas = [], set()

    def entrada_5m(z, s):
        """Busca la entrada en 5 min sobre la zona de 15 min s. Devuelve la fila o None."""
        lado = s["lado"]
        ini = idx.searchsorted(s["conocido"])
        toque = None
        for k in range(ini, min(ini + smc.VENTANA_ESPERA, len(d))):
            if (lado < 0 and c[k] > s["ob"]) or (lado > 0 and c[k] < s["ob"]):
                return None
            if toque is None:
                if (lado < 0 and h[k] >= s["borde"]) or (lado > 0 and l[k] <= s["borde"]):
                    toque = k
                continue
            if k - toque > smc.VENTANA_ENTRADA:
                if not flexible:
                    return None
                toque = None                      # flexible: espera un nuevo test de la zona
                continue
            if lado < 0:
                gatillo = c[k] < o[k] and c[k] < l[k - 6:k].min()
            else:
                gatillo = c[k] > o[k] and c[k] > h[k - 6:k].max()
            if not gatillo:
                continue
            if k in usadas or not en_mañana_ny(idx[k]) or k + smc.MAX_DURACION >= len(d):
                if flexible:
                    continue                      # flexible: espera otra señal dentro del horario
                return None
            ext = h[toque:k + 1].max() if lado < 0 else l[toque:k + 1].min()
            stop = ext - lado * 0.1 * atr5[k]
            riesgo = abs(c[k] - stop)
            if riesgo <= 0 or (s["extremo"] - c[k]) * lado <= 0:
                if flexible:
                    continue
                return None                       # sin recorrido hasta el objetivo
            usadas.add(k)
            costo = PIP / riesgo
            fila = dict(fecha=idx[k], lado=lado, tipo_1h=z.get("tipo", "completa"),
                        tipo_15m=s.get("tipo", "completa"), riesgo_pips=round(riesgo / PIP, 1),
                        rr_estructura=round(abs(s["extremo"] - c[k]) / riesgo, 2),
                        R_estructura=resultado(h, l, c, k, lado, c[k], stop, s["extremo"]) - costo,
                        R_1a2=resultado(h, l, c, k, lado, c[k], stop,
                                        c[k] + lado * 2 * riesgo) - costo)
            if (z["extremo"] - c[k]) * lado > 0:           # objetivo en la estructura de 1 h
                fila["rr_1h"] = round(abs(z["extremo"] - c[k]) / riesgo, 2)
                fila["R_estructura_1h"] = resultado(h, l, c, k, lado, c[k], stop, z["extremo"]) - costo
            else:
                fila["rr_1h"], fila["R_estructura_1h"] = np.nan, np.nan
            for e in smc.EXP_BINARIAS:
                dif = (c[k + e] - c[k]) * lado
                fila[f"bin_{e * 5}m"] = np.nan if dif == 0 else float(dif > 0)
            return fila
        return None

    for z in s1h:
        lado = z["lado"]
        # 2a) test de la zona de 1 h en velas de 15 min
        i0 = v15.index.searchsorted(z["conocido"])
        test = None
        for k in range(i0, len(v15)):
            if v15.index[k] > z["conocido"] + ESPERA_TEST_1H:
                break
            if (lado < 0 and c15[k] > z["ob"]) or (lado > 0 and c15[k] < z["ob"]):
                break
            if (lado < 0 and h15[k] >= z["borde"]) or (lado > 0 and l15[k] <= z["borde"]):
                test = v15.index[k]
                break
        if test is None:
            continue
        # 2b) nueva confirmación de 15 min después del test
        desde = int(np.searchsorted(s15_ini, test.value))
        cand = [s for s in s15[desde:desde + 400] if s["lado"] == lado and s["inicio"] >= test
                and s["conocido"] <= test + ESPERA_CONF_15]
        for s in (cand if flexible else cand[:1]):
            tramo = v15[(v15.index >= test) & (v15.index < s["conocido"])]["Close"]
            if (lado < 0 and (tramo > z["ob"]).any()) or (lado > 0 and (tramo < z["ob"]).any()):
                break                                 # la zona de 1 h ya se anuló
            # 3) entrada en 5 min al testear la zona de 15 min
            fila = entrada_5m(z, s)
            if fila:
                ops.append(fila)
                break
    return pd.DataFrame(ops)


def uno_por_dia(x):
    if x.empty:
        return x
    dia = pd.to_datetime(x["fecha"], utc=True).dt.tz_convert("America/New_York").dt.date
    return x.assign(_d=dia.values).sort_values("fecha").drop_duplicates("_d").drop(columns="_d")


def bloque(x, col):
    x = x.dropna(subset=[col]) if col in x else x.iloc[0:0]
    if x.empty:
        return "sin operaciones"
    sin_costo = (x[col] + 1 / x["riesgo_pips"]).mean()
    b = " · ".join(f"{m}m {100 * x[f'bin_{m}m'].mean():.0f}%" for m in (15, 30, 60))
    return (f"{len(x)} ops · gana {100 * (x[col] > 0).mean():.0f}% · R medio {x[col].mean():+.2f} "
            f"(sin spread {sin_costo:+.2f}) · total {x[col].sum():+.1f}R\n      binarias: {b}")


def main():
    df = cargar()
    print("Velas cargadas:", len(df), df.index[0], "->", df.index[-1])
    corte = df.index[0] + (df.index[-1] - df.index[0]) * 2 / 3
    meses = (df.index[-1] - df.index[0]).days / 30.4

    comp = completa(df)
    inter = completa(df, confirmaciones_amplias, flexible=True)
    inter.to_csv("operaciones_smc_ny_intermedia.csv", index=False)
    inter1 = uno_por_dia(inter)
    bot.en_horario = en_mañana_ny
    simple = pd.DataFrame(smc.operaciones_par("EURUSD", df))
    comp.to_csv("operaciones_smc_ny.csv", index=False)

    lineas = [
        "🔬 <b>Video SMC · EUR/USD · apertura de Nueva York (8:00-8:30)</b>",
        f"Datos reales HistData: {df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d} (~{meses:.0f} meses)",
        "Entrada solo de 8:00 a 8:30 a. m. hora NY en velas de 5 min · spread 1 pip.",
        "Gana dinero si R medio > 0 · binarias necesitan ~54 %.\n",
        "<b>COMPLETA 1h→15m→5m, TP en estructura</b>",
    ]
    if not comp.empty:
        lineas.append(f"  (objetivo típico: estructura 15m = {comp['rr_estructura'].median():.1f}x "
                      f"el riesgo · estructura 1h = {comp['rr_1h'].median():.1f}x)")
    for etiqueta, col in (("TP estructura 15m", "R_estructura"),
                          ("TP estructura 1h", "R_estructura_1h"), ("TP 1:2", "R_1a2")):
        lineas.append(f"  [{etiqueta}]")
        for tramo, x in (("Entreno", comp[comp["fecha"] < corte] if not comp.empty else comp),
                         ("Prueba", comp[comp["fecha"] >= corte] if not comp.empty else comp)):
            lineas.append(f"  {tramo}: {bloque(x, col)}")
    for titulo, base in (("INTERMEDIA 1h→15m→5m (cualquier confirmación), 1 operación por día", inter1),
                         ("INTERMEDIA sin límite diario", inter)):
        lineas.append(f"\n<b>{titulo}</b>")
        for etiqueta, col in (("TP estructura 15m", "R_estructura"), ("TP 1:2", "R_1a2")):
            lineas.append(f"  [{etiqueta}]")
            for tramo, x in (("Entreno", base[base["fecha"] < corte] if not base.empty else base),
                             ("Prueba", base[base["fecha"] >= corte] if not base.empty else base)):
                lineas.append(f"  {tramo}: {bloque(x, col)}")
    lineas.append("\n<b>SIMPLE 15m→5m, TP 1:2</b> (para comparar)")
    for tramo, x in (("Entreno", simple[simple["fecha"] < corte] if not simple.empty else simple),
                     ("Prueba", simple[simple["fecha"] >= corte] if not simple.empty else simple)):
        lineas.append(f"  {tramo}: {bloque(x, 'R_1a2')}")
    if not inter1.empty:
        inter1 = inter1.assign(tri=pd.to_datetime(inter1["fecha"], utc=True).dt.tz_localize(None).dt.to_period("Q"))
        t = inter1.groupby("tri")["R_1a2"].agg(["count", "sum"])
        lineas.append("\n<b>Intermedia (1/día, 1:2), R por trimestre:</b> " +
                      ", ".join(f"{p}: {s:+.1f}R ({int(n)})" for p, (n, s) in t.iterrows()))
    if not comp.empty:
        comp["tri"] = pd.to_datetime(comp["fecha"], utc=True).dt.tz_localize(None).dt.to_period("Q")
        t = comp.groupby("tri")["R_estructura"].agg(["count", "sum"])
        lineas.append("\n<b>Completa, R por trimestre:</b> " +
                      ", ".join(f"{p}: {s:+.1f}R ({int(n)})" for p, (n, s) in t.iterrows()))
    lineas.append("\nR = lo que arriesgas por operación (ej.: +1R = ganaste lo que arriesgaste).")
    texto = "\n".join(lineas)
    bot.enviar(texto[:4000])
    print(texto)


if __name__ == "__main__":
    sys.exit(main())
