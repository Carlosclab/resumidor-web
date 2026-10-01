"""Capa de servicio: envuelve la canalización evaluada, no la reimplementa.

El [ADR-005 §5] del proyecto de investigación fija que la plataforma web
consume la **misma** canalización que los experimentos, y el [ADR-001] que
sirve la **configuración que ganó** el criterio calidad/costo de la Fase 3.
Este módulo es la frontera: importa de `resumidor` y no duplica nada.

Las opciones que ve el usuario son las configuraciones del factorial, con su
perfil de costo medido sobre 300 artículos. Eso es lo que pide el objetivo
específico 8 por «opciones predefinidas de procesamiento».
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from resumidor.config import ExperimentoConfig, cargar_config
from resumidor.domain import Document, Section, SummaryResult
from resumidor.runner import (
    construir_estrategia,
    construir_modelo,
    ejecutar_documento,
)

# --- límites de entrada -------------------------------------------------
# El artículo más largo del corpus son 110.531 tokens. Sin un tope explícito,
# un pegado enorme deja al usuario esperando sin final. Se expresa en
# caracteres porque hay que rechazar ANTES de tokenizar.
MIN_CARACTERES = 200
MAX_CARACTERES = 400_000

# Un token de BART equivale a ~0,70 palabras (1,43 tokens por palabra),
# medido sobre el corpus en el EDA 02. Sirve para estimar sin cargar el
# tokenizador, que es lo que la pantalla de opciones necesita.
TOKENS_POR_PALABRA = 1.43


@dataclass(frozen=True)
class Preset:
    """Una opción predefinida, con su perfil de costo medido.

    Las cifras salen de `experiments/results/`, 300 artículos por celda. Son
    de la máquina del experimento (MPS); en el servidor, que es CPU, las
    latencias son varias veces mayores. Por eso la interfaz las presenta como
    referencia del experimento y muestra además el tiempo real transcurrido.
    """

    id: str
    nombre: str
    modelo_corto: str
    estrategia_corta: str
    invocaciones: str
    latencia_mediana_s: float
    memoria_gb: float
    rouge1: float
    recomendado: bool = False
    nota: str = ""


# Ordenados como se muestran: primero el recomendado.
PRESETS: tuple[Preset, ...] = (
    Preset(
        id="extractive_abstractive_bart",
        nombre="Extractivo-abstractivo · BART",
        modelo_corto="BART",
        estrategia_corta="Extractivo-abstractivo",
        invocaciones="1 invocación",
        latencia_mediana_s=6.6,
        memoria_gb=3.1,
        rouge1=0.423,
        recomendado=True,
        nota="Mejor compromiso entre calidad y costo sobre 300 artículos.",
    ),
    Preset(
        id="truncation_bart",
        nombre="Truncamiento · BART",
        modelo_corto="BART",
        estrategia_corta="Truncamiento",
        invocaciones="1 invocación",
        latencia_mediana_s=6.7,
        memoria_gb=3.1,
        rouge1=0.405,
        nota="Descarta todo lo que no cabe en la ventana de 1.024 tokens.",
    ),
    Preset(
        id="truncation_pegasus",
        nombre="Truncamiento · PEGASUS",
        modelo_corto="PEGASUS",
        estrategia_corta="Truncamiento",
        invocaciones="1 invocación",
        latencia_mediana_s=11.1,
        memoria_gb=4.2,
        rouge1=0.456,
        nota="La mejor calidad del experimento, al triple de latencia.",
    ),
    Preset(
        id="map_reduce_bart",
        nombre="Map-reduce · BART",
        modelo_corto="BART",
        estrategia_corta="Map-reduce",
        invocaciones="~10 invocaciones",
        latencia_mediana_s=33.6,
        memoria_gb=3.2,
        rouge1=0.398,
        nota="Procesa el artículo entero por fragmentos. No mejoró la calidad.",
    ),
    Preset(
        id="lead_k",
        nombre="Primeras frases · sin modelo",
        modelo_corto="—",
        estrategia_corta="Lead-k",
        invocaciones="0 invocaciones",
        latencia_mediana_s=0.0,
        memoria_gb=0.0,
        rouge1=0.350,
        nota="Extrae las primeras frases. Instantáneo, y el piso del experimento.",
    ),
)

PRESET_POR_DEFECTO = "extractive_abstractive_bart"
_PRESETS_POR_ID = {p.id: p for p in PRESETS}


class EntradaInvalida(ValueError):
    """El texto recibido no se puede resumir. El mensaje va al usuario."""


@dataclass(frozen=True)
class Resumen:
    """Lo que la interfaz necesita mostrar.

    Lleva las medidas de costo porque el RT-2 del proyecto exige que ninguna
    ruta devuelva un resumen sin ellas — y porque mostrarlas convierte la
    plataforma en evidencia del propio experimento.
    """

    texto: str
    preset: str
    modelo: str
    estrategia: str
    latencia_s: float
    memoria_mb: float
    invocaciones: int
    tokens_entrada: int
    tokens_salida: int
    ratio_compresion: float

    @classmethod
    def desde(cls, r: SummaryResult, preset: str) -> Resumen:
        return cls(
            texto=r.summary,
            preset=preset,
            modelo=r.model_name,
            estrategia=r.strategy_name,
            latencia_s=round(r.cost.latency_seconds, 1),
            memoria_mb=round(r.cost.peak_memory_mb, 1),
            invocaciones=r.cost.model_calls,
            tokens_entrada=r.cost.input_tokens,
            tokens_salida=r.cost.output_tokens,
            ratio_compresion=round(r.compression_ratio, 4),
        )


def ruta_config(nombre: str) -> Path:
    """Localiza el YAML de una configuración.

    Busca primero dentro del paquete instalado y cae al árbol del repositorio
    de investigación en desarrollo local. Las «opciones predefinidas de
    procesamiento» del objetivo específico 8 son literalmente estos archivos.
    """
    try:
        empaquetada = Path(str(files("resumidor") / "_configs" / f"{nombre}.yaml"))
        if empaquetada.is_file():
            return empaquetada
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    for raiz in (Path("experiments/configs"), Path("../repo/experiments/configs")):
        candidata = raiz / f"{nombre}.yaml"
        if candidata.is_file():
            return candidata

    raise FileNotFoundError(
        f"No se encontró la configuración {nombre!r}. En desarrollo local, "
        "ejecuta desde la raíz del repositorio web con el de investigación "
        "como hermano."
    )


def validar(texto: str) -> str:
    """Normaliza y comprueba la entrada antes de gastar cómputo."""
    limpio = (texto or "").strip()
    if len(limpio) < MIN_CARACTERES:
        raise EntradaInvalida(
            f"El texto es demasiado corto ({len(limpio)} caracteres). "
            f"Se necesitan al menos {MIN_CARACTERES} para que el resumen "
            "tenga sentido."
        )
    if len(limpio) > MAX_CARACTERES:
        raise EntradaInvalida(
            f"El texto supera el límite de {MAX_CARACTERES:,} caracteres "
            f"({len(limpio):,} recibidos). Pega solo el cuerpo del artículo, "
            "sin la bibliografía."
        )
    return limpio


def analizar(texto: str) -> dict:
    """Estadísticas del texto, sin cargar el tokenizador.

    La pantalla de opciones las necesita para que el usuario vea el tamaño de
    lo que va a resumir antes de decidir la configuración.
    """
    limpio = validar(texto)
    palabras = len(limpio.split())
    return {
        "caracteres": len(limpio),
        "palabras": palabras,
        "tokens_estimados": round(palabras * TOKENS_POR_PALABRA),
    }


def preset(nombre: str | None) -> Preset:
    elegido = nombre or PRESET_POR_DEFECTO
    if elegido not in _PRESETS_POR_ID:
        raise EntradaInvalida(
            f"Configuración desconocida: {elegido!r}. "
            f"Disponibles: {', '.join(_PRESETS_POR_ID)}."
        )
    return _PRESETS_POR_ID[elegido]


@dataclass
class _Cargado:
    config: ExperimentoConfig
    modelo: object
    estrategia: object


class Resumidor:
    """Mantiene los modelos cargados y resuelve una petición a la vez.

    Dos invariantes que importan:

    1. **Un modelo por checkpoint, compartido entre configuraciones.** Las
       tres estrategias de BART usan el mismo checkpoint: cargarlo una vez
       ahorra 1,5 GB por cada una. `HFSummarizer` cachea los pesos por
       instancia, así que la clave de la caché es el checkpoint, no el preset.
    2. **Una generación a la vez.** El cerrojo serializa el acceso: dos
       peticiones concurrentes sobre el mismo modelo compiten por CPU y
       duplican la latencia de ambas.
    """

    def __init__(self) -> None:
        self._cargados: dict[str, _Cargado] = {}
        self._modelos: dict[str, object] = {}
        self._cerrojo = threading.Lock()
        self._listo = False

    @property
    def listo(self) -> bool:
        return self._listo

    def _preparar(self, preset_id: str) -> _Cargado:
        """Carga perezosa por preset, compartiendo modelo entre estrategias."""
        if preset_id in self._cargados:
            return self._cargados[preset_id]

        config = cargar_config(ruta_config(preset_id))
        checkpoint = config.model.checkpoint

        if checkpoint not in self._modelos:
            modelo = construir_modelo(config)
            estrategia_tmp = construir_estrategia(config)
            usa_pesos = getattr(estrategia_tmp, "usa_modelo_generativo", True)
            modelo.precargar(pesos=usa_pesos)
            self._modelos[checkpoint] = modelo

        cargado = _Cargado(
            config=config,
            modelo=self._modelos[checkpoint],
            estrategia=construir_estrategia(config),
        )
        self._cargados[preset_id] = cargado
        return cargado

    def precargar(self) -> None:
        """Prepara el preset por defecto al arrancar.

        No es opcional: sin el calentamiento, la primera petición paga la
        compilación del grafo del dispositivo. Los demás presets se cargan
        bajo demanda, para no tener dos checkpoints en memoria sin usarlos.
        """
        self._preparar(PRESET_POR_DEFECTO)
        self._listo = True

    def resumir(self, texto: str, preset_id: str | None = None) -> Resumen:
        elegido = preset(preset_id)
        limpio = validar(texto)
        documento = Document(
            doc_id=f"web-{uuid.uuid4().hex[:12]}",
            sections=(Section(title="", text=limpio),),
        )
        with self._cerrojo:
            cargado = self._preparar(elegido.id)
            resultado = ejecutar_documento(
                documento, cargado.modelo, cargado.estrategia
            )
        return Resumen.desde(resultado, elegido.id)


@lru_cache(maxsize=1)
def obtener_resumidor() -> Resumidor:
    """Instancia única del proceso."""
    return Resumidor()
