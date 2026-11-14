"""
Crea el proyecto Android de la app "Señales Brandy" dentro de la carpeta app/.
Lo ejecuta GitHub (workflow app.yml) antes de construir la APK. No hace falta ejecutarlo a mano.
La app lee las señales que publica bot.py en la rama app-senales (senales_app.json).
"""
import base64
import os

ARCHIVOS = {}

ARCHIVOS['app/settings.gradle'] = r'''pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositories { google(); mavenCentral() }
}
rootProject.name = "SenalesBrandy"
include ':movil'
'''

ARCHIVOS['app/build.gradle'] = r'''plugins {
    id 'com.android.application' version '8.2.2' apply false
}
'''

ARCHIVOS['app/gradle.properties'] = r'''org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8
android.nonTransitiveRClass=true
'''

ARCHIVOS['app/movil/build.gradle'] = r'''plugins {
    id 'com.android.application'
}

def numero = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInteger()

android {
    namespace 'com.brandy.senales'
    compileSdk 34

    defaultConfig {
        applicationId "com.brandy.senales"
        minSdk 26
        targetSdk 34
        versionCode numero
        versionName "1." + numero
    }

    // Firma fija: así cada versión nueva se instala encima de la anterior sin perder los filtros.
    signingConfigs {
        fija {
            storeFile file("firma.p12")
            storePassword "senales123"
            keyAlias "senales"
            keyPassword "senales123"
            storeType "pkcs12"
        }
    }

    buildTypes {
        debug { signingConfig signingConfigs.fija }
        release {
            minifyEnabled false
            signingConfig signingConfigs.fija
        }
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }

    lint {
        abortOnError false
        checkReleaseBuilds false
    }
}
'''

ARCHIVOS['app/movil/src/main/AndroidManifest.xml'] = r'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
    <uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" />

    <application
        android:label="Señales Brandy"
        android:icon="@drawable/icono"
        android:allowBackup="true"
        android:theme="@android:style/Theme.DeviceDefault.NoActionBar">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:launchMode="singleTask"
            android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <service
            android:name=".ServicioSenales"
            android:exported="false"
            android:foregroundServiceType="specialUse">
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="Avisos de senales de trading en tiempo real" />
        </service>

        <receiver
            android:name=".Arranque"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
            </intent-filter>
        </receiver>
    </application>
</manifest>
'''

ARCHIVOS['app/movil/src/main/res/drawable/icono.xml'] = r'''<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="48dp" android:height="48dp"
    android:viewportWidth="48" android:viewportHeight="48">
    <path android:fillColor="#3B5BDB"
        android:pathData="M8,2h32a6,6 0 0 1 6,6v32a6,6 0 0 1 -6,6h-32a6,6 0 0 1 -6,-6v-32a6,6 0 0 1 6,-6z"/>
    <path android:fillColor="#00000000" android:strokeColor="#FFFFFF" android:strokeWidth="3.5"
        android:strokeLineCap="round" android:strokeLineJoin="round"
        android:pathData="M10,33 L19,24 L26,30 L37,17"/>
    <path android:fillColor="#FFFFFF" android:pathData="M30,14 L40,14 L40,24 Z"/>
</vector>
'''

ARCHIVOS['app/movil/src/main/assets/index.html'] = r'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Señales Brandy</title>
<style>
:root {
  --fondo: #f4f5f7; --tarjeta: #ffffff; --texto: #16181d; --suave: #646b78; --borde: #e2e5ea;
  --verde: #12805c; --verde-bg: #e3f5ee; --rojo: #c4322b; --rojo-bg: #fbe7e5;
  --gris-bg: #eef0f3; --acento: #3b5bdb; --acento-bg: #e8edff; --sombra: 0 1px 3px rgba(0,0,0,.08);
}
@media (prefers-color-scheme: dark) {
  :root {
    --fondo: #0f1115; --tarjeta: #191c22; --texto: #eceef2; --suave: #9aa1ad; --borde: #2a2e37;
    --verde: #3ccf91; --verde-bg: #12302a; --rojo: #ff6b61; --rojo-bg: #3a1c1a;
    --gris-bg: #252932; --acento: #8ea6ff; --acento-bg: #1f2747; --sombra: none;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--fondo); color: var(--texto);
  font: 15px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: var(--tarjeta); border-bottom: 1px solid var(--borde);
  padding: 12px 16px 0; }
.fila { display: flex; align-items: center; gap: 8px; }
.titulo { font-size: 19px; font-weight: 700; flex: 1; }
.estado { font-size: 12px; color: var(--suave); margin-top: 2px; }
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
.btn-icono { width: 38px; height: 38px; border-radius: 10px; background: var(--gris-bg); font-size: 18px; }
.pestanas { display: flex; margin-top: 10px; }
.pestanas button { flex: 1; padding: 10px 0; font-weight: 600; color: var(--suave); border-bottom: 3px solid transparent; }
.pestanas button.activa { color: var(--acento); border-bottom-color: var(--acento); }
main { padding: 12px 16px 40px; max-width: 640px; margin: 0 auto; }
.vista { display: none; } .vista.activa { display: block; }
.dia { font-size: 13px; font-weight: 700; color: var(--suave); text-transform: uppercase; letter-spacing: .04em; margin: 16px 2px 8px; }
.tarjeta { background: var(--tarjeta); border: 1px solid var(--borde); border-radius: 14px; padding: 12px 14px;
  margin-bottom: 10px; box-shadow: var(--sombra); }
.cab { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--suave); }
.cab .estr { flex: 1; font-weight: 600; }
.hora { font-size: 13px; font-weight: 700; color: var(--texto); }
.par { display: flex; align-items: center; gap: 10px; margin: 6px 0 4px; }
.par b { font-size: 22px; letter-spacing: .02em; }
.dir { font-weight: 700; font-size: 14px; padding: 3px 10px; border-radius: 999px; }
.dir.CALL { color: var(--verde); background: var(--verde-bg); }
.dir.PUT { color: var(--rojo); background: var(--rojo-bg); }
.info { font-size: 13px; color: var(--suave); }
.detalle { font-size: 14px; margin: 6px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--gris-bg); color: var(--suave); }
.chip.favor { color: var(--verde); background: var(--verde-bg); }
.chip.contra { color: var(--rojo); background: var(--rojo-bg); }
.res { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--borde); font-size: 14px; }
.res .ganada { color: var(--verde); font-weight: 600; } .res .perdida { color: var(--rojo); font-weight: 600; }
.res .curso { color: var(--suave); }
.enlaces { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.enlaces a { font-size: 12.5px; text-decoration: none; padding: 6px 10px; border-radius: 9px;
  background: var(--acento-bg); color: var(--acento); font-weight: 600; }
.enlaces a.tv { background: var(--texto); color: var(--tarjeta); }
.vacio { text-align: center; color: var(--suave); padding: 40px 12px; }
.bloque { background: var(--tarjeta); border: 1px solid var(--borde); border-radius: 14px; padding: 14px; margin-bottom: 12px; }
.bloque h3 { margin: 0 0 10px; font-size: 15px; }
.opciones { display: flex; flex-wrap: wrap; gap: 8px; }
.op { padding: 8px 12px; border-radius: 10px; border: 1px solid var(--borde); font-size: 14px; }
.op.si { background: var(--acento-bg); border-color: var(--acento); color: var(--acento); font-weight: 600; }
.interruptor { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 0; }
.interruptor small { display: block; color: var(--suave); font-size: 12.5px; }
.sw { position: relative; width: 48px; height: 28px; flex: none; border-radius: 999px; background: var(--borde); transition: .15s; }
.sw::after { content: ""; position: absolute; top: 3px; left: 3px; width: 22px; height: 22px; border-radius: 50%; background: #fff; transition: .15s; }
.sw.si { background: var(--acento); } .sw.si::after { left: 23px; }
select { font: inherit; color: var(--texto); background: var(--gris-bg); border: 1px solid var(--borde); border-radius: 9px; padding: 7px 10px; }
.est { display: grid; grid-template-columns: 1fr auto; gap: 2px 10px; padding: 8px 0; border-bottom: 1px solid var(--borde); }
.est:last-child { border-bottom: 0; }
.est .pct { font-weight: 700; } .barra { grid-column: 1 / -1; height: 6px; border-radius: 3px; background: var(--gris-bg); overflow: hidden; }
.barra i { display: block; height: 100%; background: var(--acento); }
.nota { font-size: 12.5px; color: var(--suave); margin-top: 8px; }
</style>
</head>
<body>
<header>
  <div class="fila">
    <div style="flex:1">
      <div class="titulo">📈 Señales Brandy</div>
      <div class="estado" id="estado">Cargando…</div>
    </div>
    <button class="btn-icono" id="btnAvisos" title="Avisos">🔔</button>
    <button class="btn-icono" id="btnActualizar" title="Actualizar">⟳</button>
  </div>
  <nav class="pestanas">
    <button data-vista="senales" class="activa">Señales</button>
    <button data-vista="estadisticas">Estadísticas</button>
    <button data-vista="filtros">Filtros</button>
  </nav>
</header>
<main>
  <section class="vista activa" id="v-senales"><div id="lista"></div></section>
  <section class="vista" id="v-estadisticas"><div id="stats"></div></section>
  <section class="vista" id="v-filtros"><div id="filtros"></div></section>
</main>
<script>
"use strict";
const URL_FEED = "https://api.github.com/repos/brandyherrera944-art/bot-senales/contents/senales_app.json?ref=app-senales";
const ESTRATEGIAS = [
  ["RSI2", "🔍", "RSI2"], ["LZ", "🧠", "RSI2 + Lorentzian"], ["SR", "🧱", "Soportes/Resistencias"],
  ["ENV", "🕯️", "Vela envolvente"], ["DIV", "🔀", "Divergencia"], ["PRINCIPAL", "📊", "Bot principal"]];
const PARES = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"];
const PLATAFORMAS = [["Quotex", "https://qxbroker.com"], ["IQ Option", "https://iqoption.com"],
  ["Pepperstone", "https://pepperstone.com"], ["OANDA", "https://www.oanda.com"], ["XM", "https://www.xm.com"]];
const MOTIVOS = { horario: "fuera del horario del filtro", aprendizaje: "condición mala aprendida" };
const POR_DEFECTO = { estrategias: ESTRATEGIAS.map(e => e[0]), pares: PARES.slice(), exp: "todas",
  desde: 2, hasta: 15, soloAFavor: false, incluirNoAvisadas: false, avisos: true };
const A = window.Android || null;          // puente con la parte Android (si existe)
let feed = null, filtros = leerFiltros(), etag = "";

function leerFiltros() {
  let f = null;
  try { f = JSON.parse(A ? A.leerFiltros() : localStorage.getItem("filtros")); } catch (e) {}
  return Object.assign({}, POR_DEFECTO, f || {});
}
function guardarFiltros() {
  const t = JSON.stringify(filtros);
  try { if (A) A.guardarFiltros(t); else localStorage.setItem("filtros", t); } catch (e) {}
}
function parBonito(p) { return p.slice(0, 3) + "/" + p.slice(3); }
function esc(t) { return String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function pasaFiltro(s, f) {
  if (!f.estrategias.includes(s.estrategia) || !f.pares.includes(s.par)) return false;
  if (f.exp !== "todas" && !s.expiraciones.includes(parseInt(f.exp))) return false;
  const h = parseInt(s.hora); if (h < f.desde || h > f.hasta) return false;
  if (f.soloAFavor && s.tendencia !== "a favor") return false;
  if (!f.incluirNoAvisadas && !s.avisada) return false;
  return true;
}
function abrir(url) { if (A && A.abrir) { A.abrir(url); return false; } return true; }

// ------------------------------------------------------------------ datos
async function cargar(forzar) {
  if (A) {
    if (forzar && A.actualizarAhora) A.actualizarAhora();
    try { const t = A.obtenerFeed(); if (t) feed = JSON.parse(t); } catch (e) {}
    pintar(); return;
  }
  try {
    const h = { Accept: "application/vnd.github.raw" };
    if (etag && !forzar) h["If-None-Match"] = etag;
    const r = await fetch(URL_FEED, { headers: h, cache: "no-store" });
    if (r.status === 200) { etag = r.headers.get("ETag") || ""; feed = await r.json(); }
    else if (r.status !== 304) document.getElementById("estado").textContent = "Sin conexión con las señales (" + r.status + ")";
  } catch (e) { document.getElementById("estado").textContent = "Sin conexión a internet"; }
  pintar();
}

// ---------------------------------------------------------------- pantalla
function pintarEstado() {
  const el = document.getElementById("estado");
  if (!feed) { el.textContent = "Todavía no hay datos del bot"; return; }
  const min = Math.round((Date.now() - new Date(feed.actualizado)) / 60000);
  const hace = min < 1 ? "hace menos de 1 min" : min < 60 ? `hace ${min} min` :
    min < 1440 ? `hace ${Math.round(min / 60)} h` : `hace ${Math.round(min / 1440)} días`;
  el.textContent = `Actualizado ${hace} · avisos ${filtros.avisos ? "activados" : "apagados"}`;
  document.getElementById("btnAvisos").textContent = filtros.avisos ? "🔔" : "🔕";
}
function tarjeta(s) {
  const tend = s.tendencia ? `<span class="chip ${s.tendencia === "a favor" ? "favor" : s.tendencia === "en contra" ? "contra" : ""}">Tendencia ${esc(s.tendencia)}</span>` : "";
  const noAv = s.avisada ? "" : `<span class="chip">No avisada en Telegram · ${esc(MOTIVOS[s.motivo] || s.motivo)}</span>`;
  const res = s.expiraciones.map(e => {
    const r = s.resultados[String(e)], q = s.que_paso[String(e)];
    if (!r) return `<span class="curso">⏳ ${e} min: en curso</span>`;
    if (r === "GANADA") return `<span class="ganada">✅ ${e} min: ganada</span>`;
    if (r === "EMPATE") return `<span class="curso">➖ ${e} min: empate</span>`;
    return `<span class="perdida">❌ ${e} min: perdida${q ? ` · <span style="font-weight:400">${esc(q.toLowerCase())}</span>` : ""}</span>`;
  }).join("");
  const tv = `https://es.tradingview.com/chart/?symbol=OANDA:${s.par}`;
  const enlaces = `<a class="tv" href="${tv}" onclick="return abrir(this.href)">📈 TradingView</a>` +
    PLATAFORMAS.map(([n, u]) => `<a href="${u}" onclick="return abrir(this.href)">${n}</a>`).join("");
  return `<article class="tarjeta">
    <div class="cab"><span>${s.emoji}</span><span class="estr">${esc(s.nombre)}</span><span class="hora">⏱️ ${esc(s.hora)}</span></div>
    <div class="par"><b>${parBonito(s.par)}</b><span class="dir ${s.lado}">${s.lado === "CALL" ? "⬆️ Arriba" : "⬇️ Abajo"}</span></div>
    <div class="info">Expiración ${s.expiraciones.join(" y ")} min · Precio ${esc(s.precio)}</div>
    ${s.detalle ? `<div class="detalle">📌 ${esc(s.detalle)}</div>` : ""}
    <div class="chips">${tend}${noAv}</div>
    <div class="res">${res}</div>
    <div class="enlaces">${enlaces}</div>
  </article>`;
}
function nombreDia(fecha) {
  const hoy = new Date(), d = new Date(fecha + "T12:00:00");
  const dif = Math.round((new Date(hoy.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (dif === 0) return "Hoy"; if (dif === 1) return "Ayer";
  return d.toLocaleDateString("es-PE", { weekday: "long", day: "numeric", month: "long" });
}
function pintarLista() {
  const cont = document.getElementById("lista");
  if (!feed) { cont.innerHTML = `<div class="vacio">Esperando las primeras señales del bot…<br><br>El bot trabaja de lunes a viernes, de 2 am a 3 pm (hora Perú).</div>`; return; }
  const ss = feed.senales.filter(s => pasaFiltro(s, filtros)).sort((a, b) => b.entrada_utc.localeCompare(a.entrada_utc));
  if (!ss.length) { cont.innerHTML = `<div class="vacio">No hay señales con tus filtros.<br><br>Revisa la pestaña <b>Filtros</b>.</div>`; return; }
  let html = "", dia = "";
  for (const s of ss) {
    if (s.fecha !== dia) { dia = s.fecha; html += `<div class="dia">${nombreDia(dia)}</div>`; }
    html += tarjeta(s);
  }
  cont.innerHTML = html;
}
function pintarStats() {
  const cont = document.getElementById("stats");
  if (!feed) { cont.innerHTML = `<div class="vacio">Sin datos todavía.</div>`; return; }
  let html = `<div class="bloque"><h3>Con tus filtros (señales en la app)</h3>`;
  const ss = feed.senales.filter(s => pasaFiltro(s, filtros));
  for (const [cod, emoji, nombre] of ESTRATEGIAS) {
    const x = ss.filter(s => s.estrategia === cod);
    let g = 0, n = 0;
    for (const s of x) for (const r of Object.values(s.resultados)) { if (r === "GANADA") { g++; n++; } else if (r === "PERDIDA") n++; }
    if (!n) continue;
    const p = Math.round(100 * g / n);
    html += `<div class="est"><span>${emoji} ${nombre}</span><span class="pct">${g}/${n} · ${p}%</span><div class="barra"><i style="width:${p}%"></i></div></div>`;
  }
  html += `<div class="nota">Cuenta 15 y 30 min por separado. Solo las señales que guarda la app (las últimas 300).</div></div>`;
  html += `<div class="bloque"><h3>Acumulado real desde que empezó cada estrategia</h3>`;
  for (const f of feed.estadisticas || []) {
    const p = f.n ? Math.round(100 * f.g / f.n) : 0;
    html += `<div class="est"><span>${f.emoji} ${esc(f.nombre)} · ${esc(f.detalle)}</span><span class="pct">${f.n ? `${f.g}/${f.n} · ${p}%` : "sin datos"}</span><div class="barra"><i style="width:${p}%"></i></div></div>`;
  }
  html += `<div class="nota">Meta: 56 % o más. Para no perder en binarias con pago de 85 % hace falta más de 54 %.</div></div>`;
  cont.innerHTML = html;
}
function pintarFiltros() {
  const op = (grupo, val, txt, si) => `<button class="op ${si ? "si" : ""}" data-g="${grupo}" data-v="${val}">${txt}</button>`;
  const sw = (clave, titulo, sub) => `<div class="interruptor"><div>${titulo}<small>${sub}</small></div><button class="sw ${filtros[clave] ? "si" : ""}" data-sw="${clave}" aria-label="${titulo}"></button></div>`;
  const horas = Array.from({ length: 14 }, (_, i) => i + 2);
  const sel = (clave) => `<select data-sel="${clave}">${horas.map(h => `<option value="${h}" ${filtros[clave] === h ? "selected" : ""}>${h}:00</option>`).join("")}</select>`;
  document.getElementById("filtros").innerHTML = `
    <div class="bloque"><h3>Estrategias</h3><div class="opciones">${ESTRATEGIAS.map(([c, e, n]) => op("estrategias", c, `${e} ${n}`, filtros.estrategias.includes(c))).join("")}</div></div>
    <div class="bloque"><h3>Pares</h3><div class="opciones">${PARES.map(p => op("pares", p, parBonito(p), filtros.pares.includes(p))).join("")}</div></div>
    <div class="bloque"><h3>Expiración</h3><div class="opciones">${[["todas", "15 y 30 min"], ["15", "Solo 15 min"], ["30", "Solo 30 min"]].map(([v, t]) => op("exp", v, t, filtros.exp === v)).join("")}</div></div>
    <div class="bloque"><h3>Horario de entrada (hora Perú)</h3><div class="fila">Desde ${sel("desde")} hasta ${sel("hasta")}</div>
      <div class="nota">Ejemplo: desde 2:00 hasta 3:00 muestra las entradas de 2:00 a 3:59.</div></div>
    <div class="bloque">
      ${sw("soloAFavor", "Solo a favor de la tendencia", "Oculta las señales que van contra la tendencia de 1 hora")}
      ${sw("incluirNoAvisadas", "Mostrar también las no avisadas", "Incluye las que el bot midió en silencio (fuera de horario o por lo aprendido)")}
      ${sw("avisos", "Avisos con notificación", "Te avisa al instante cuando llega una señal que pasa tus filtros")}
    </div>`;
}
function pintar() { pintarEstado(); pintarLista(); pintarStats(); }

// ------------------------------------------------------------------ eventos
document.querySelector(".pestanas").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  document.querySelectorAll(".pestanas button").forEach(x => x.classList.toggle("activa", x === b));
  document.querySelectorAll(".vista").forEach(v => v.classList.toggle("activa", v.id === "v-" + b.dataset.vista));
  window.scrollTo(0, 0);
});
document.getElementById("filtros").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.sw) {
    filtros[b.dataset.sw] = !filtros[b.dataset.sw];
    if (b.dataset.sw === "avisos" && A && A.activarAvisos) A.activarAvisos(filtros.avisos);
  } else if (b.dataset.g === "exp") filtros.exp = b.dataset.v;
  else if (b.dataset.g) {
    const lista = filtros[b.dataset.g], v = b.dataset.v, i = lista.indexOf(v);
    if (i >= 0) { if (lista.length > 1) lista.splice(i, 1); } else lista.push(v);   // al menos una
  }
  guardarFiltros(); pintarFiltros(); pintar();
});
document.getElementById("filtros").addEventListener("change", e => {
  const s = e.target.closest("select"); if (!s) return;
  filtros[s.dataset.sel] = parseInt(s.value);
  if (filtros.desde > filtros.hasta) { if (s.dataset.sel === "desde") filtros.hasta = filtros.desde; else filtros.desde = filtros.hasta; }
  guardarFiltros(); pintarFiltros(); pintar();
});
document.getElementById("btnActualizar").addEventListener("click", () => cargar(true));
document.getElementById("btnAvisos").addEventListener("click", () => {
  filtros.avisos = !filtros.avisos; guardarFiltros();
  if (A && A.activarAvisos) A.activarAvisos(filtros.avisos);
  pintarFiltros(); pintarEstado();
});
window.alActualizar = () => cargar(false);     // la parte Android lo llama cuando llegan datos nuevos
pintarFiltros(); cargar(true);
setInterval(() => cargar(false), A ? 10000 : 60000);
if (A && A.actualizarAhora) setInterval(() => A.actualizarAhora(), 30000);   // con la app abierta
setInterval(pintarEstado, 30000);
</script>
</body>
</html>
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/Ajustes.java'] = r'''package com.brandy.senales;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/** Filtros guardados (los elige la pantalla) y lectura de los ajustes. */
class Ajustes {
    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("ajustes", Context.MODE_PRIVATE);
    }

    static String filtros(Context c) {
        return prefs(c).getString("filtros", null);
    }

    static void guardarFiltros(Context c, String json) {
        prefs(c).edit().putString("filtros", json).apply();
    }

    static JSONObject filtrosJson(Context c) {
        try {
            String t = filtros(c);
            return t == null ? new JSONObject() : new JSONObject(t);
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    static boolean avisosActivos(Context c) {
        return filtrosJson(c).optBoolean("avisos", true);
    }
}
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/Almacen.java'] = r'''package com.brandy.senales;

import android.content.Context;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;

/** Guarda la última copia del archivo de señales en el teléfono. */
class Almacen {
    private static File archivo(Context c) {
        return new File(c.getFilesDir(), "feed.json");
    }

    static synchronized String leerFeed(Context c) {
        File f = archivo(c);
        if (!f.exists()) {
            return null;
        }
        try (FileInputStream in = new FileInputStream(f)) {
            return ServicioSenales.leerTodo(in);
        } catch (Exception e) {
            return null;
        }
    }

    static synchronized void guardarFeed(Context c, String texto) {
        try (FileOutputStream out = new FileOutputStream(archivo(c))) {
            out.write(texto.getBytes("UTF-8"));
        } catch (Exception e) {
            // si no se puede guardar, se intenta en la próxima descarga
        }
    }
}
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/Arranque.java'] = r'''package com.brandy.senales;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Al encender el teléfono, vuelve a activar los avisos (si estaban activados). */
public class Arranque extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) && Ajustes.avisosActivos(c)) {
            ServicioSenales.iniciar(c);
        }
    }
}
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/Filtro.java'] = r'''package com.brandy.senales;

import org.json.JSONArray;
import org.json.JSONObject;

/** Los mismos filtros que la pantalla (pasaFiltro en index.html), para decidir qué notificar. */
class Filtro {
    static boolean pasa(JSONObject s, JSONObject f) {
        if (!contiene(f.optJSONArray("estrategias"), s.optString("estrategia"))) {
            return false;
        }
        if (!contiene(f.optJSONArray("pares"), s.optString("par"))) {
            return false;
        }
        String exp = f.optString("exp", "todas");
        if (!"todas".equals(exp)) {
            JSONArray ex = s.optJSONArray("expiraciones");
            boolean ok = false;
            for (int j = 0; ex != null && j < ex.length(); j++) {
                if (String.valueOf(ex.optInt(j)).equals(exp)) {
                    ok = true;
                }
            }
            if (!ok) {
                return false;
            }
        }
        int hora;
        try {
            hora = Integer.parseInt(s.optString("hora", "00:00").substring(0, 2));
        } catch (Exception e) {
            return false;
        }
        if (hora < f.optInt("desde", 2) || hora > f.optInt("hasta", 15)) {
            return false;
        }
        if (f.optBoolean("soloAFavor", false) && !"a favor".equals(s.optString("tendencia"))) {
            return false;
        }
        if (!f.optBoolean("incluirNoAvisadas", false) && !s.optBoolean("avisada", true)) {
            return false;
        }
        return true;
    }

    /** Sin lista (filtros nunca guardados) = todo permitido. */
    private static boolean contiene(JSONArray lista, String valor) {
        if (lista == null) {
            return true;
        }
        for (int i = 0; i < lista.length(); i++) {
            if (valor.equals(lista.optString(i))) {
                return true;
            }
        }
        return false;
    }
}
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/MainActivity.java'] = r'''package com.brandy.senales;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Pantalla principal: muestra index.html (lista de señales, filtros y estadísticas). */
public class MainActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        web.addJavascriptInterface(new Puente(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                abrirFuera(request.getUrl().toString());     // los enlaces se abren en el navegador
                return true;
            }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");

        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        pedirSinAhorroDeBateria();
        if (Ajustes.avisosActivos(this)) {
            ServicioSenales.iniciar(this);
        }
        ServicioSenales.descargarEnSegundoPlano(getApplicationContext());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) {
            web.evaluateJavascript("window.alActualizar && window.alActualizar()", null);
        }
    }

    /** Pide una sola vez que Android no duerma la app, para que los avisos lleguen a tiempo. */
    private void pedirSinAhorroDeBateria() {
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())
                    && !Ajustes.prefs(this).getBoolean("pidio_bateria", false)) {
                Ajustes.prefs(this).edit().putBoolean("pidio_bateria", true).apply();
                Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                i.setData(Uri.parse("package:" + getPackageName()));
                startActivity(i);
            }
        } catch (Exception e) {
            // si el teléfono no lo permite, la app sigue funcionando igual
        }
    }

    private void abrirFuera(final String url) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (ActivityNotFoundException e) {
                    // sin navegador instalado
                }
            }
        });
    }

    /** Funciones que la pantalla (JavaScript) puede llamar como window.Android.xxx(). */
    public class Puente {
        @JavascriptInterface
        public String obtenerFeed() {
            return Almacen.leerFeed(MainActivity.this);
        }

        @JavascriptInterface
        public String leerFiltros() {
            return Ajustes.filtros(MainActivity.this);
        }

        @JavascriptInterface
        public void guardarFiltros(String json) {
            Ajustes.guardarFiltros(MainActivity.this, json);
        }

        @JavascriptInterface
        public void actualizarAhora() {
            ServicioSenales.descargarEnSegundoPlano(getApplicationContext());
        }

        @JavascriptInterface
        public void activarAvisos(boolean si) {
            if (si) {
                ServicioSenales.iniciar(MainActivity.this);
            } else {
                ServicioSenales.detener(MainActivity.this);
            }
        }

        @JavascriptInterface
        public void abrir(String url) {
            abrirFuera(url);
        }
    }
}
'''

ARCHIVOS['app/movil/src/main/java/com/brandy/senales/ServicioSenales.java'] = r'''package com.brandy.senales;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.Set;

/**
 * Servicio en primer plano: cada 30 segundos revisa el archivo de señales del bot en GitHub y,
 * si llega una señal nueva que pasa los filtros, avisa con una notificación.
 */
public class ServicioSenales extends Service {
    static final String URL_FEED = "https://api.github.com/repos/brandyherrera944-art/bot-senales/"
            + "contents/senales_app.json?ref=app-senales";
    static final String CANAL_ESCUCHA = "escucha";
    static final String CANAL_SENALES = "senales";
    static final long CADA_MS = 30000L;
    static final long MAX_ANTIGUEDAD_MS = 15L * 60L * 1000L;     // no avisa señales de hace más de 15 min

    private volatile boolean activo = false;
    private Thread hilo;

    static void iniciar(Context c) {
        Intent i = new Intent(c, ServicioSenales.class);
        try {
            c.startForegroundService(i);
        } catch (Exception e) {
            // Android puede no permitirlo en este momento; se reintenta al abrir la app
        }
    }

    static void detener(Context c) {
        c.stopService(new Intent(c, ServicioSenales.class));
    }

    static void descargarEnSegundoPlano(final Context c) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    revisar(c);
                } catch (Exception e) {
                    // sin internet o GitHub no respondió: se reintenta después
                }
            }
        }).start();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        crearCanales(this);
        Notification n = new Notification.Builder(this, CANAL_ESCUCHA)
                .setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle("Señales Brandy")
                .setContentText("Escuchando las señales del bot")
                .setContentIntent(abrirApp(this))
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!activo) {
            activo = true;
            final Context c = getApplicationContext();
            hilo = new Thread(new Runnable() {
                @Override
                public void run() {
                    while (activo) {
                        try {
                            revisar(c);
                        } catch (Exception e) {
                            // se reintenta en el próximo ciclo
                        }
                        try {
                            Thread.sleep(CADA_MS);
                        } catch (InterruptedException e) {
                            return;
                        }
                    }
                }
            });
            hilo.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        activo = false;
        if (hilo != null) {
            hilo.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** Descarga el archivo (solo si cambió, gracias al ETag) y avisa las señales nuevas. */
    static synchronized void revisar(Context c) throws Exception {
        SharedPreferences p = Ajustes.prefs(c);
        HttpURLConnection con = (HttpURLConnection) new URL(URL_FEED).openConnection();
        con.setConnectTimeout(15000);
        con.setReadTimeout(20000);
        con.setUseCaches(false);
        con.setRequestProperty("Accept", "application/vnd.github.raw");
        con.setRequestProperty("User-Agent", "SenalesBrandy-Android");
        String etag = p.getString("etag", null);
        if (etag != null && Almacen.leerFeed(c) != null) {
            con.setRequestProperty("If-None-Match", etag);
        }
        try {
            int codigo = con.getResponseCode();
            if (codigo == 200) {
                String cuerpo = leerTodo(con.getInputStream());
                Almacen.guardarFeed(c, cuerpo);
                p.edit().putString("etag", con.getHeaderField("ETag")).apply();
                avisarNuevas(c, cuerpo);
            }
        } finally {
            con.disconnect();
        }
    }

    static void avisarNuevas(Context c, String cuerpo) throws Exception {
        SharedPreferences p = Ajustes.prefs(c);
        JSONArray ss = new JSONObject(cuerpo).getJSONArray("senales");
        Set<String> vistos = new HashSet<>(p.getStringSet("vistos", new HashSet<String>()));
        Set<String> actuales = new HashSet<>();
        boolean primeraVez = !p.getBoolean("iniciado", false);
        boolean avisos = Ajustes.avisosActivos(c);
        JSONObject filtros = Ajustes.filtrosJson(c);
        long ahora = System.currentTimeMillis();
        for (int i = 0; i < ss.length(); i++) {
            JSONObject s = ss.getJSONObject(i);
            String id = s.optString("id");
            actuales.add(id);
            if (vistos.contains(id)) {
                continue;
            }
            vistos.add(id);
            if (primeraVez || !avisos) {
                continue;
            }
            long entrada;
            try {
                entrada = OffsetDateTime.parse(s.optString("entrada_utc")).toInstant().toEpochMilli();
            } catch (Exception e) {
                continue;
            }
            if (ahora - entrada > MAX_ANTIGUEDAD_MS || !Filtro.pasa(s, filtros)) {
                continue;
            }
            notificar(c, s);
        }
        vistos.retainAll(actuales);
        p.edit().putStringSet("vistos", vistos).putBoolean("iniciado", true).apply();
    }

    static void notificar(Context c, JSONObject s) {
        String par = s.optString("par", "??????");
        String parBonito = par.length() == 6 ? par.substring(0, 3) + "/" + par.substring(3) : par;
        String dir = "CALL".equals(s.optString("lado")) ? "⬆️ Arriba" : "⬇️ Abajo";
        JSONArray ex = s.optJSONArray("expiraciones");
        StringBuilder exps = new StringBuilder();
        for (int j = 0; ex != null && j < ex.length(); j++) {
            if (j > 0) {
                exps.append(" y ");
            }
            exps.append(ex.optInt(j));
        }
        String titulo = s.optString("emoji") + " " + parBonito + " " + dir + " · entrada " + s.optString("hora");
        String texto = s.optString("nombre") + " · " + exps + " min · precio " + s.optString("precio");
        String largo = texto + "\n" + s.optString("detalle") + "\nTendencia: " + s.optString("tendencia");
        Notification n = new Notification.Builder(c, CANAL_SENALES)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle(titulo)
                .setContentText(texto)
                .setStyle(new Notification.BigTextStyle().bigText(largo))
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setAutoCancel(true)
                .setContentIntent(abrirApp(c))
                .build();
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm != null) {
            nm.notify(s.optString("id").hashCode(), n);
        }
    }

    static void crearCanales(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) {
            return;
        }
        NotificationChannel escucha = new NotificationChannel(CANAL_ESCUCHA, "Escucha en segundo plano",
                NotificationManager.IMPORTANCE_MIN);
        escucha.setDescription("Aviso fijo mientras la app revisa las señales");
        NotificationChannel senales = new NotificationChannel(CANAL_SENALES, "Señales nuevas",
                NotificationManager.IMPORTANCE_HIGH);
        senales.setDescription("Una notificación por cada señal que pasa tus filtros");
        senales.enableVibration(true);
        nm.createNotificationChannel(escucha);
        nm.createNotificationChannel(senales);
    }

    static PendingIntent abrirApp(Context c) {
        Intent i = new Intent(c, MainActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(c, 0, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    static String leerTodo(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
        in.close();
        return out.toString("UTF-8");
    }
}
'''

FIRMA_P12 = "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"

if __name__ == "__main__":
    for ruta, texto in ARCHIVOS.items():
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
    with open("app/movil/firma.p12", "wb") as f:
        f.write(base64.b64decode(FIRMA_P12))
    print(f"Proyecto creado: {len(ARCHIVOS) + 1} archivos en app/")
