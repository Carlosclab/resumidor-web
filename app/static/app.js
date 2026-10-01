/* Resumidor científico — lógica de la interfaz.
 *
 * Cuatro pantallas excluyentes: entrada → opciones → proceso → resultado.
 */

const $ = (id) => document.getElementById(id);

const PANTALLAS = ["entrada", "opciones", "proceso", "resultado"];
const mostrar = (cual) =>
  PANTALLAS.forEach((p) => ($(`pantalla-${p}`).hidden = p !== cual));

const estado = {
  limites: { min: 200, max: 400000 },
  presets: [],
  elegido: null,
  resumen: null,
};

const esp = (n, dec = 0) =>
  n.toLocaleString("es", { minimumFractionDigits: dec, maximumFractionDigits: dec });

function avisar(mensaje) {
  const el = $("aviso");
  el.textContent = mensaje;
  el.hidden = !mensaje;
}

/* ─────────────── paso 1: entrada ─────────────── */
function alEscribir() {
  const n = $("texto").value.trim().length;
  $("contador").textContent = `${esp(n)} caracteres`;
  const corto = n < estado.limites.min;
  const largo = n > estado.limites.max;
  $("continuar").disabled = corto || largo;
  avisar(largo ? `El texto supera el límite de ${esp(estado.limites.max)} caracteres.` : "");
}

async function continuar() {
  try {
    const r = await fetch("/api/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto: $("texto").value }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail);

    $("documento-meta").textContent =
      `${esp(d.palabras)} palabras · ~${esp(d.tokens_estimados)} tokens`;
    mostrar("opciones");
  } catch (e) {
    avisar(e.message);
  }
}

/* ─────────────── paso 2: opciones ─────────────── */
function perfil(p) {
  const partes = [p.invocaciones];
  if (p.latencia_mediana_s > 0) partes.push(`~${esp(p.latencia_mediana_s, 1)} s`);
  if (p.memoria_gb > 0) partes.push(`~${esp(p.memoria_gb, 1)} GB`);
  return partes.join(" · ");
}

function pintarOpciones() {
  const rec = estado.presets.find((p) => p.recomendado) || estado.presets[0];
  $("recomendada").innerHTML = `
    <div class="recomendada__rotulo">Recomendada</div>
    <div class="recomendada__nombre">${rec.nombre}</div>
    <div class="recomendada__costo">${perfil(rec)}</div>`;

  $("presets").innerHTML = estado.presets
    .map(
      (p) => `
      <label class="opcion">
        <input type="radio" name="preset" value="${p.id}"
               ${p.id === estado.elegido ? "checked" : ""}>
        <span>
          ${p.nombre}
          <span class="opcion__costo">${perfil(p)} · ROUGE-1 ${esp(p.rouge1, 3)}</span>
        </span>
      </label>`
    )
    .join("");

  $("presets").addEventListener("change", (ev) => {
    estado.elegido = ev.target.value;
    const p = estado.presets.find((x) => x.id === estado.elegido);
    $("recomendada").innerHTML = `
      <div class="recomendada__rotulo">${p.recomendado ? "Recomendada" : "Elegida"}</div>
      <div class="recomendada__nombre">${p.nombre}</div>
      <div class="recomendada__costo">${perfil(p)}</div>`;
  });
}

/* ─────────────── paso 3: proceso ─────────────── */
/* La configuración por defecto hace UNA invocación, así que no hay progreso
 * real que reportar desde dentro. La barra avanza por etapas y se detiene en
 * el 92 % hasta que llega la respuesta: nunca finge haber terminado.
 * El cronómetro sí es real, y la mediana del experimento calibra la espera. */
let cronometro = null;
let etapas = [];

function arrancarProceso(p) {
  const inicio = performance.now();
  $("barra").style.width = "0%";
  $("cronometro").textContent = "0,0 s";
  $("referencia").textContent =
    p.latencia_mediana_s > 0
      ? `La mediana de esta configuración en el experimento es de unos ` +
        `${esp(p.latencia_mediana_s, 1)} s. En el servidor, que no tiene GPU, ` +
        `suele tardar más. No cierres esta pestaña.`
      : "Esta configuración no usa modelo generativo: es casi instantánea.";

  cronometro = setInterval(() => {
    $("cronometro").textContent = `${esp((performance.now() - inicio) / 1000, 1)} s`;
  }, 100);

  const guion = [
    [12, "Paso 1 de 4 · Preparando el texto"],
    [34, `Paso 2 de 4 · Seleccionando las frases centrales`],
    [68, `Paso 3 de 4 · Generando con ${p.modelo_corto} (${p.invocaciones})`],
    [92, "Paso 4 de 4 · Afinando la redacción"],
  ];
  etapas = guion.map(([pct, texto], i) =>
    setTimeout(() => {
      $("barra").style.width = `${pct}%`;
      $("paso").textContent = texto;
    }, i * 2200)
  );
}

function detenerProceso() {
  clearInterval(cronometro);
  etapas.forEach(clearTimeout);
  etapas = [];
}

async function resumir() {
  const p = estado.presets.find((x) => x.id === estado.elegido);
  mostrar("proceso");
  arrancarProceso(p);

  try {
    const r = await fetch("/api/resumir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto: $("texto").value, preset: estado.elegido }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "No se pudo generar el resumen.");

    detenerProceso();
    $("barra").style.width = "100%";
    estado.resumen = d;
    pintarResultado(d);
    mostrar("resultado");
  } catch (e) {
    detenerProceso();
    mostrar("opciones");
    alert(e.message);
  }
}

/* ─────────────── paso 4: resultado ─────────────── */
function pintarResultado(d) {
  $("resultado").textContent = d.texto;
  const filas = [
    [`${esp(d.latencia_s, 1)} s`, "Latencia"],
    [d.memoria_mb > 0 ? `${esp(d.memoria_mb / 1024, 1)} GB` : "—", "Memoria pico"],
    [d.invocaciones, "Invocaciones al modelo"],
    [
      `${esp(d.ratio_compresion * 100, 1)} %`,
      `Compresión (${esp(d.tokens_entrada)} → ${esp(d.tokens_salida)})`,
    ],
  ];
  $("metricas").innerHTML = filas
    .map(([v, k]) => `<div><dt>${v}</dt><dd>${k}</dd></div>`)
    .join("");
}

function descargar() {
  const d = estado.resumen;
  const cabecera =
    `Resumen generado por el Resumidor de artículos científicos\n` +
    `Universidad de Antioquia — Grupo 2, Los Predictores\n\n` +
    `Modelo: ${d.modelo}\nEstrategia: ${d.estrategia}\n` +
    `Latencia: ${esp(d.latencia_s, 1)} s · Invocaciones: ${d.invocaciones}\n` +
    `Tokens: ${esp(d.tokens_entrada)} → ${esp(d.tokens_salida)}\n\n` +
    `${"-".repeat(60)}\n\n`;
  const url = URL.createObjectURL(
    new Blob([cabecera + d.texto], { type: "text/plain;charset=utf-8" })
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "resumen.txt";
  a.click();
  URL.revokeObjectURL(url);
}

/* ─────────────── ejemplo ─────────────── */
const EJEMPLO = `the main properties of the ultraluminous x - ray sources ( ulxs ) are their huge luminosities and the diversity of their x - ray spectra . the nature of these objects is still debated . they could be intermediate mass black holes accreting at sub - eddington rates , or stellar mass black holes accreting at super - eddington rates with beamed emission . we present a systematic analysis of a sample of ulxs observed with xmm - newton . the spectra are fitted with a combination of a multicolour disc blackbody and a power law component . we find that the inner disc temperatures are systematically higher than those expected for intermediate mass black holes , which favours the super - eddington accretion scenario . we also detect spectral curvature at high energies in several sources , consistent with an optically thick corona . ulxs may be supercritical accretion disks observed close to the disk axis in close binaries with a stellar mass black hole , or microquasars . similar to ss433 , ulxs are connected with nebulae , and new data show the nebulae are expanding . we compare the gas nebula around ss433 with nebulae of ulxs in holmberg ii , ngc 6946 and ic 342 , observed recently with integral field spectroscopy . the results suggest that most ulxs are stellar mass black holes in a distinct accretion state , rather than a new class of compact objects . further observations with higher signal to noise are required to confirm the presence of the high energy rollover in the fainter members of the sample .`;

/* ─────────────── arranque ─────────────── */
async function iniciar() {
  $("texto").addEventListener("input", alEscribir);
  $("continuar").addEventListener("click", continuar);
  $("volver").addEventListener("click", () => mostrar("entrada"));
  $("resumir").addEventListener("click", resumir);
  $("descargar").addEventListener("click", descargar);
  $("otro").addEventListener("click", () => {
    mostrar("entrada");
    $("texto").focus();
  });
  $("ejemplo").addEventListener("click", () => {
    $("texto").value = EJEMPLO;
    alEscribir();
  });
  $("copiar").addEventListener("click", async () => {
    await navigator.clipboard.writeText(estado.resumen.texto);
    const b = $("copiar");
    b.textContent = "Copiado ✓";
    setTimeout(() => (b.textContent = "Copiar resumen"), 1600);
  });

  document.querySelectorAll("[data-abrir]").forEach((b) =>
    b.addEventListener("click", () => $(b.dataset.abrir).showModal())
  );
  document.querySelectorAll("[data-cerrar]").forEach((b) =>
    b.addEventListener("click", () => b.closest("dialog").close())
  );

  try {
    const cfg = await (await fetch("/api/configuracion")).json();
    estado.limites = { min: cfg.min_caracteres, max: cfg.max_caracteres };
    estado.presets = cfg.presets;
    estado.elegido = cfg.por_defecto;
    pintarOpciones();
  } catch {
    avisar("No se pudo cargar la configuración del servicio.");
  }
  alEscribir();
}

iniciar();
