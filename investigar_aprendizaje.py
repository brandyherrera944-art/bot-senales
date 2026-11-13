"""
¿Puede el bot aprender de sus operaciones? Prueba con ~3 años de datos reales (HistData).

Toma TODAS las señales de la vigilancia RSI2 (USD/CAD 15 y 30 min, GBP/USD 30 min) de enero 2024
a septiembre 2026 y, para cada una, calcula los mismos datos que guarda bot.py al aprender:
hora, día, dirección, fuerza del RSI2, ADX, volatilidad, distancia a la EMA200 y hora de noticias.

Después compara ganadas contra perdidas en cada grupo:
  Entreno = primeros 2/3 del tiempo · Prueba = último 1/3 (días que la regla "no vio").
Una condición solo sirve si es buena (o mala) en entreno Y en prueba a la vez.

Guarda aprendizaje_historial.csv, que bot.py usa como "memoria de 3 años" para no dejarse
engañar por rachas cortas.

Uso:
  python investigar_aprendizaje.py           -> descarga HistData (en GitHub Actions)
  python investigar_aprendizaje.py rapido    -> solo hora/día/dirección/noticias, desde
                                                operaciones_rsi2.csv (sin descargar nada)
Análisis educativo, no es asesoría financiera.
"""

import sys

import numpy as np
import pandas as pd

import bot

CLAVES = [("USDCAD", 15), ("USDCAD", 30), ("GBPUSD", 30)]   # las de la vigilancia RSI2
ARCHIVO = bot.ARCHIVO_HISTORIAL


def operaciones_con_contexto(par, df5):
    """Señales RSI2 (con resultado) y el contexto de cada una, calculado como en bot.py."""
    import investigar_rsi2 as ir
    ops = ir.operaciones(par, df5)
    if ops.empty:
        return ops
    df = bot.indicadores(df5)
    ctxs = []
    for fecha, lado in zip(ops["fecha"], ops["lado"]):
        ts = pd.Timestamp(fecha) - pd.Timedelta(minutes=bot.MIN_VELA)   # vela de la señal
        i = df.index.get_loc(ts)
        ctxs.append(bot.contexto(df, i, 1 if lado == "CALL" else -1))
    extra = pd.DataFrame(ctxs).drop(columns=["lado", "noticias"])   # ya vienen en ops
    return pd.concat([ops.reset_index(drop=True), extra], axis=1)


def contexto_basico(ops):
    """Sin precios: solo lo que se puede sacar de la fecha, la dirección y las noticias."""
    entrada = pd.to_datetime(ops["fecha"], utc=True)
    local = entrada.dt.tz_convert("America/Bogota")
    ops = ops.copy()
    ops["hora"] = local.dt.hour
    ops["dia"] = local.dt.dayofweek
    return ops


def historial(ops):
    """Cuenta ganadas/total por condición y periodo (entreno/prueba) para cada clave."""
    ops = ops.copy()
    ops["f"] = pd.to_datetime(ops["fecha"], utc=True)
    corte = ops["f"].min() + (ops["f"].max() - ops["f"].min()) * 2 / 3
    ops["periodo"] = np.where(ops["f"] < corte, "entreno", "prueba")
    g = pd.DataFrame([bot.grupos(r) for r in ops.to_dict("records")], index=ops.index)
    filas = []
    for par, e in CLAVES:
        col = f"r_{e}"
        if col not in ops:
            continue
        x = ops[(ops["par"] == par) & ops[col].notna()]
        clave = bot.clave_de("RSI2", par, e)
        for var in bot.VARIABLES:
            if var not in g or var == "par":
                continue
            gx = g.loc[x.index, var]
            for (grupo, periodo), y in x.groupby([gx, x["periodo"]]):
                filas.append(dict(clave=clave, variable=var, grupo=grupo, periodo=periodo,
                                  ganadas=int(y[col].sum()), total=len(y)))
    return pd.DataFrame(filas), corte


def informe(h, corte):
    lineas = ["🧠 <b>¿Puede aprender el bot? Prueba con ~3 años (RSI2)</b>",
              f"Entreno = antes del {corte:%Y-%m-%d} · Prueba = después.",
              "Una condición sirve solo si es ≥56 % (o ≤44 %) en entreno Y en prueba."]
    hallazgos = []
    for clave, x in h.groupby("clave"):
        p = x.pivot_table(index=["variable", "grupo"], columns="periodo",
                          values=["ganadas", "total"], aggfunc="sum").fillna(0)
        mejores = []
        for (var, grupo), f in p.iterrows():
            ne, np_ = f[("total", "entreno")], f[("total", "prueba")]
            if ne < 100 or np_ < 100:
                continue
            e, q = f[("ganadas", "entreno")] / ne, f[("ganadas", "prueba")] / np_
            marca = "✅" if e >= 0.56 and q >= 0.56 else ("❌" if e <= 0.44 and q <= 0.44 else "")
            if marca:
                hallazgos.append(f"{marca} {bot.nombre_clave(clave)} · {bot.VARIABLES[var]} {grupo}: "
                                 f"{100 * e:.0f} % / {100 * q:.0f} %")
            mejores.append((min(e, q), var, grupo, e, q, int(ne + np_)))
        mejores.sort(reverse=True)
        lineas.append(f"\n<b>{bot.nombre_clave(clave)}</b> · las 3 más constantes (entreno/prueba):")
        for _, var, grupo, e, q, n in mejores[:3]:
            lineas.append(f"• {bot.VARIABLES[var]} {grupo}: {100 * e:.0f} % / {100 * q:.0f} % ({n} ops)")
    lineas.append("")
    if hallazgos:
        lineas.append("<b>Condiciones que se repiten en entreno y prueba:</b>")
        lineas += hallazgos[:12]
    else:
        lineas.append("❌ Ninguna condición pasa de 56 % (ni baja de 44 %) en entreno y prueba a la vez: "
                      "con estos datos no hay nada fiable que aprender todavía.")
    lineas.append("Guardado en aprendizaje_historial.csv: el bot lo usa para no confiar en rachas.")
    lineas.append("Análisis educativo, no es asesoría financiera.")
    return "\n".join(lineas)


def main(rapido=False):
    if rapido:
        ops = contexto_basico(pd.read_csv("operaciones_rsi2.csv"))
    else:
        import investigar_rsi2 as ir
        partes = []
        for par in sorted({p for p, _ in CLAVES}):
            df = ir.velas_5m(par)
            if df is None or len(df) < 1000:
                print(f"{par}: sin datos suficientes", flush=True)
                continue
            print(f"{par}: {len(df)} velas de 5 min {df.index[0]} -> {df.index[-1]}", flush=True)
            partes.append(operaciones_con_contexto(par, df))
        if not partes:
            bot.enviar("⚠️ Prueba de aprendizaje: no se pudieron descargar datos de HistData.")
            return 1
        ops = pd.concat(partes, ignore_index=True)
    h, corte = historial(ops)
    h.to_csv(ARCHIVO, index=False)
    texto = informe(h, corte)
    print(texto)
    if not rapido:
        bot.enviar(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main(rapido=len(sys.argv) > 1 and sys.argv[1] == "rapido"))
