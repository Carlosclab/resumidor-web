"""API HTTP del resumidor.

FastAPI y no Gradio a propósito: la interfaz debe poder adoptar el diseño
propio del equipo, y Gradio impone su estética. Aquí el HTML es nuestro.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.servicio import (
    MAX_CARACTERES,
    MIN_CARACTERES,
    EntradaInvalida,
    obtener_resumidor,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("resumidor")

ESTATICOS = Path(__file__).parent / "static"


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Carga el modelo ANTES de aceptar tráfico.

    Cloud Run envía peticiones en cuanto el contenedor responde al sondeo de
    arranque. Si el modelo se cargara perezosamente, el primer usuario
    pagaría la descarga de 1,5 GB más la compilación del grafo.
    """
    resumidor = obtener_resumidor()
    log.info("Cargando %s …", resumidor.config.model.checkpoint)
    resumidor.precargar()
    log.info(
        "Listo · modelo=%s estrategia=%s dispositivo=%s",
        resumidor.config.model.checkpoint,
        resumidor.config.strategy.kind,
        resumidor.config.model.device,
    )
    yield


app = FastAPI(
    title="Resumidor de artículos científicos",
    description="Grupo 2 · Los Predictores — Proyecto Integrador I, UdeA",
    lifespan=ciclo_de_vida,
)


class PeticionResumen(BaseModel):
    texto: str = Field(min_length=1, max_length=MAX_CARACTERES * 2)


@app.get("/salud")
def salud() -> dict:
    """Sondeo para Cloud Run. Distingue «arrancando» de «listo»."""
    r = obtener_resumidor()
    return {"listo": r.listo, "modelo": r.config.model.checkpoint}


@app.get("/api/configuracion")
def configuracion() -> dict:
    """Qué está sirviendo la plataforma, y los límites de entrada."""
    r = obtener_resumidor()
    return {
        "configuracion": r.config.id,
        "modelo": r.config.model.checkpoint,
        "estrategia": r.config.strategy.kind,
        "min_caracteres": MIN_CARACTERES,
        "max_caracteres": MAX_CARACTERES,
    }


@app.post("/api/resumir")
def resumir(peticion: PeticionResumen) -> dict:
    r = obtener_resumidor()
    if not r.listo:
        raise HTTPException(
            status_code=503,
            detail="El modelo todavía se está cargando. Inténtalo en unos segundos.",
        )
    try:
        resumen = r.resumir(peticion.texto)
    except EntradaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception:
        log.exception("Fallo al resumir")
        raise HTTPException(
            status_code=500, detail="No se pudo generar el resumen."
        ) from None

    log.info(
        "resumen · %d→%d tokens · %.1fs · %d invocación(es)",
        resumen.tokens_entrada,
        resumen.tokens_salida,
        resumen.latencia_s,
        resumen.invocaciones,
    )
    return {
        "resumen": resumen.texto,
        "metricas": {
            "modelo": resumen.modelo,
            "estrategia": resumen.estrategia,
            "latencia_s": resumen.latencia_s,
            "invocaciones": resumen.invocaciones,
            "tokens_entrada": resumen.tokens_entrada,
            "tokens_salida": resumen.tokens_salida,
            "ratio_compresion": resumen.ratio_compresion,
        },
    }


@app.get("/")
def inicio() -> FileResponse:
    return FileResponse(ESTATICOS / "index.html")


app.mount("/estatico", StaticFiles(directory=ESTATICOS), name="estatico")


if __name__ == "__main__":
    import uvicorn

    # Cloud Run inyecta PORT; en local cae a 8080.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
