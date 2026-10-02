"""API HTTP del resumidor.

FastAPI y no Gradio a propósito: la interfaz debe poder adoptar el diseño
propio del equipo, y Gradio impone su estética. Aquí el HTML es nuestro.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.servicio import (
    MAX_CARACTERES,
    MIN_CARACTERES,
    PRESET_POR_DEFECTO,
    PRESETS,
    EntradaInvalida,
    analizar,
    obtener_resumidor,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("resumidor")

ESTATICOS = Path(__file__).parent / "static"


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Abre el puerto de inmediato y carga el modelo en segundo plano.

    Cargar antes de escuchar parece lo correcto, pero rompe el arranque en
    Cloud Run: su sondeo de arranque intenta conectar al puerto 8080 y expira
    con DEADLINE_EXCEEDED mientras el calentamiento genera texto en CPU. La
    instancia nunca llega a levantarse.

    Así que el puerto se abre ya y la carga va en un hilo. `/salud` informa
    con `listo` si el modelo está disponible, y `/api/resumir` responde 503
    mientras tanto: la interfaz espera y lo muestra, en vez de que la
    instancia entera muera.
    """
    resumidor = obtener_resumidor()

    def cargar() -> None:
        log.info("Cargando el preset por defecto (%s) …", PRESET_POR_DEFECTO)
        try:
            resumidor.precargar()
            log.info("Listo para atender peticiones")
        except Exception:
            log.exception("Fallo al precargar el modelo")

    threading.Thread(target=cargar, name="precarga", daemon=True).start()
    yield


app = FastAPI(
    title="Resumidor de artículos científicos",
    description="Grupo 2 · Los Predictores — Proyecto Integrador I, UdeA",
    lifespan=ciclo_de_vida,
)


class PeticionTexto(BaseModel):
    texto: str = Field(min_length=1, max_length=MAX_CARACTERES * 2)


class PeticionResumen(PeticionTexto):
    preset: str | None = None


@app.get("/salud")
def salud() -> dict:
    """Sondeo para Cloud Run. Distingue «arrancando» de «listo»."""
    return {"listo": obtener_resumidor().listo}


@app.get("/api/configuracion")
def configuracion() -> dict:
    """Las opciones predefinidas y sus perfiles de costo.

    Las cifras son del experimento (300 artículos por celda) y la interfaz
    las presenta como tal: sirven para calibrar la espera, no como promesa.
    """
    return {
        "presets": [dataclasses.asdict(p) for p in PRESETS],
        "por_defecto": PRESET_POR_DEFECTO,
        "min_caracteres": MIN_CARACTERES,
        "max_caracteres": MAX_CARACTERES,
    }


@app.post("/api/analizar")
def analizar_texto(peticion: PeticionTexto) -> dict:
    try:
        return analizar(peticion.texto)
    except EntradaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/api/resumir")
def resumir(peticion: PeticionResumen) -> dict:
    r = obtener_resumidor()
    if not r.listo:
        raise HTTPException(
            status_code=503,
            detail="El modelo todavía se está cargando. Inténtalo en unos segundos.",
        )
    try:
        resumen = r.resumir(peticion.texto, peticion.preset)
    except EntradaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception:
        log.exception("Fallo al resumir")
        raise HTTPException(
            status_code=500, detail="No se pudo generar el resumen."
        ) from None

    log.info(
        "%s · %d→%d tokens · %.1fs · %d invocación(es)",
        resumen.preset,
        resumen.tokens_entrada,
        resumen.tokens_salida,
        resumen.latencia_s,
        resumen.invocaciones,
    )
    return dataclasses.asdict(resumen)


@app.get("/")
def inicio() -> FileResponse:
    return FileResponse(ESTATICOS / "index.html")


app.mount("/estatico", StaticFiles(directory=ESTATICOS), name="estatico")


if __name__ == "__main__":
    import uvicorn

    # Cloud Run inyecta PORT; en local cae a 8080.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
