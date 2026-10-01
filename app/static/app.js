/* Resumidor científico — lógica de la interfaz.
 *
 * Tres pasos excluyentes (entrada → proceso → resultado), al modo de
 * iLovePDF: en cada momento hay una sola cosa que hacer.
 */

const $ = (id) => document.getElementById(id);

const pasos = {
  entrada: $("paso-entrada"),
  proceso: $("paso-proceso"),
  resultado: $("paso-resultado"),
};

let limites = { min: 200, max: 400000 };

function mostrar(cual) {
  for (const [nombre, el] of Object.entries(pasos)) el.hidden = nombre !== cual;
}

function avisar(mensaje) {
  const el = $("aviso");
  el.textContent = mensaje;
  el.hidden = !mensaje;
}

/* --- contador y habilitación del botón --- */
function alEscribir() {
  const n = $("texto").value.trim().length;
  $("contador").textContent = `${n.toLocaleString("es")} caracteres`;
  $("resumir").disabled = n < limites.min;
  if (n > limites.max) {
    avisar(`El texto supera el límite de ${limites.max.toLocaleString("es")} caracteres.`);
    $("resumir").disabled = true;
  } else {
    avisar("");
  }
}

/* --- progreso por etapas ---
 * La configuración desplegada hace UNA invocación al modelo, así que no hay
 * progreso real que reportar desde dentro. La barra avanza por etapas con
 * tiempos tomados de la latencia medida (p50 6,6 s / p95 7,7 s) y se detiene
 * en el 90 % hasta que llega la respuesta: nunca finge haber terminado.
 */
const ETAPAS = [
  [10, "Analizando el documento"],
  [35, "Seleccionando las frases centrales"],
  [65, "Generando el resumen"],
  [90, "Afinando la redacción"],
];

let temporizadores = [];

function arrancarProgreso() {
  detenerProgreso();
  $("barra").style.width = "0%";
  ETAPAS.forEach(([pct, texto], i) => {
    temporizadores.push(
      setTimeout(() => {
        $("barra").style.width = `${pct}%`;
        $("proceso-detalle").textContent = texto;
      }, i * 1800)
    );
  });
}

function detenerProgreso() {
  temporizadores.forEach(clearTimeout);
  temporizadores = [];
}

/* --- acción principal --- */
async function resumir() {
  const texto = $("texto").value.trim();
  mostrar("proceso");
  arrancarProgreso();

  try {
    const r = await fetch("/api/resumir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    });

    const datos = await r.json();
    if (!r.ok) throw new Error(datos.detail || "No se pudo generar el resumen.");

    detenerProgreso();
    $("barra").style.width = "100%";
    $("resultado").textContent = datos.resumen;
    pintarMetricas(datos.metricas);
    mostrar("resultado");
  } catch (e) {
    detenerProgreso();
    mostrar("entrada");
    avisar(e.message);
  }
}

function pintarMetricas(m) {
  const filas = [
    ["Modelo", m.modelo],
    ["Estrategia", m.estrategia],
    ["Tiempo de generación", `${m.latencia_s} s`],
    ["Invocaciones al modelo", m.invocaciones],
    ["Tokens de entrada", m.tokens_entrada.toLocaleString("es")],
    ["Tokens del resumen", m.tokens_salida.toLocaleString("es")],
    ["Ratio de compresión", `${(m.ratio_compresion * 100).toFixed(1)} %`],
  ];
  $("metricas").innerHTML = filas
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
    .join("");
}

/* --- ejemplo --- */
const EJEMPLO = `the main properties of the ultraluminous x - ray sources ( ulxs ) are their huge luminosities and the diversity of their x - ray spectra . the nature of these objects is still debated . they could be intermediate mass black holes accreting at sub - eddington rates , or stellar mass black holes accreting at super - eddington rates with beamed emission . we present a systematic analysis of a sample of ulxs observed with xmm - newton . the spectra are fitted with a combination of a multicolour disc blackbody and a power law component . we find that the inner disc temperatures are systematically higher than those expected for intermediate mass black holes , which favours the super - eddington accretion scenario . we also detect spectral curvature at high energies in several sources , consistent with an optically thick corona . the results suggest that most ulxs are stellar mass black holes in a distinct accretion state , rather than a new class of compact objects . further observations with higher signal to noise are required to confirm the presence of the high energy rollover in the fainter members of the sample .`;

/* --- arranque --- */
async function iniciar() {
  $("texto").addEventListener("input", alEscribir);
  $("resumir").addEventListener("click", resumir);
  $("otro").addEventListener("click", () => {
    mostrar("entrada");
    $("texto").focus();
  });
  $("ejemplo").addEventListener("click", () => {
    $("texto").value = EJEMPLO;
    alEscribir();
    $("texto").focus();
  });
  $("copiar").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("resultado").textContent);
    const b = $("copiar");
    b.textContent = "Copiado ✓";
    setTimeout(() => (b.textContent = "Copiar resumen"), 1600);
  });

  try {
    const cfg = await (await fetch("/api/configuracion")).json();
    limites = { min: cfg.min_caracteres, max: cfg.max_caracteres };
    $("barra-config").textContent = `${cfg.modelo} · ${cfg.estrategia}`;
  } catch {
    $("barra-config").textContent = "";
  }
  alEscribir();
}

iniciar();
