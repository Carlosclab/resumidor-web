"""Capa de servicio: envuelve la canalización evaluada, no la reimplementa.

El [ADR-005 §5] del proyecto de investigación fija que la plataforma web
consume la **misma** canalización que los experimentos, y el [ADR-001] que
sirve la **configuración que ganó** el criterio calidad/costo de la Fase 3.
Este módulo es la frontera: importa de `resumidor` y no duplica nada.

La configuración por defecto es `extractive_abstractive_bart`. Sobre 300
documentos entrega el 93 % del ROUGE-1 y el 97 % del ROUGE-Lsum de la mejor
celda, con una latencia p95 de 7,7 s frente a 22,8 s. La diferencia de
ROUGE-Lsum con la ganadora en calidad pura no es estadísticamente
significativa (p=0,062), así que el criterio del objetivo específico 6
—compromiso, no calidad máxima— selecciona esta.
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
# un pegado enorme deja al usuario esperando sin final. El tope se expresa en
# caracteres porque hay que rechazar ANTES de tokenizar.
MIN_CARACTERES = 200
MAX_CARACTERES = 400_000  # ~60.000 palabras, por encima del p99 del corpus

CONFIG_POR_DEFECTO = "extractive_abstractive_bart"


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
    modelo: str
    estrategia: str
    latencia_s: float
    invocaciones: int
    tokens_entrada: int
    tokens_salida: int
    ratio_compresion: float

    @classmethod
    def desde(cls, r: SummaryResult) -> Resumen:
        return cls(
            texto=r.summary,
            modelo=r.model_name,
            estrategia=r.strategy_name,
            latencia_s=round(r.cost.latency_seconds, 2),
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


class Resumidor:
    """Mantiene el modelo cargado y resuelve una petición a la vez.

    Dos invariantes que importan:

    1. **Un solo modelo por proceso.** `HFSummarizer` cachea tokenizer y pesos
       por instancia; construir uno por petición cargaría 1,5 GB cada vez.
    2. **Una generación a la vez.** El cerrojo serializa el acceso: dos
       peticiones concurrentes sobre el mismo modelo compiten por CPU y
       duplican la latencia de ambas. Encolar es el comportamiento correcto.
    """

    def __init__(self, nombre_config: str = CONFIG_POR_DEFECTO) -> None:
        self.config: ExperimentoConfig = cargar_config(ruta_config(nombre_config))
        self._modelo = construir_modelo(self.config)
        self._estrategia = construir_estrategia(self.config)
        self._cerrojo = threading.Lock()
        self._listo = False

    @property
    def listo(self) -> bool:
        return self._listo

    def precargar(self) -> None:
        """Descarga pesos y calienta el grafo. Se llama al arrancar.

        No es opcional: sin el calentamiento, la primera petición paga la
        compilación del grafo del dispositivo. Medido en el repositorio de
        investigación: 448,8 s el primer documento frente a 28,3 s el segundo.
        """
        usa_modelo = getattr(self._estrategia, "usa_modelo_generativo", True)
        self._modelo.precargar(pesos=usa_modelo)
        self._listo = True

    def resumir(self, texto: str) -> Resumen:
        limpio = validar(texto)
        documento = Document(
            doc_id=f"web-{uuid.uuid4().hex[:12]}",
            sections=(Section(title="", text=limpio),),
        )
        with self._cerrojo:
            resultado = ejecutar_documento(documento, self._modelo, self._estrategia)
        return Resumen.desde(resultado)


@lru_cache(maxsize=1)
def obtener_resumidor() -> Resumidor:
    """Instancia única del proceso."""
    return Resumidor()
