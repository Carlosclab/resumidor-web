# Resumidor de artículos científicos — plataforma web

**Grupo 2 · Los Predictores** — Proyecto Integrador I (2508700), UdeA 2026-2

MVP de la Fase 4. Pega el texto de un artículo científico en inglés y obtén un
resumen generado por un modelo preentrenado.

La investigación vive en un repositorio aparte:
[carlosforeroudea/ps1-g2](https://github.com/carlosforeroudea/ps1-g2).

---

## Qué sirve, y por qué esa configuración

`extractive_abstractive_bart` — `facebook/bart-large-cnn` con selección
extractiva previa por TextRank.

No se eligió por conveniencia. El [ADR-001] exige que la plataforma sirva la
configuración que ganó el criterio de compromiso calidad/costo del
experimento, medido sobre 300 artículos:

| Configuración | ROUGE-1 | ROUGE-Lsum | Latencia p95 | Memoria |
|---|---|---|---|---|
| truncation_pegasus | 0,456 | 0,386 | 22,8 s | 4,2 GB |
| **extractive_abstractive_bart** | **0,423** | **0,375** | **7,7 s** | **3,1 GB** |
| map_reduce_pegasus | 0,449 | 0,387 | 62,0 s | 4,3 GB |
| lead_k (sin modelo) | 0,350 | 0,307 | 0 s | — |

`truncation_pegasus` gana en ROUGE-1, pero su ventaja en ROUGE-Lsum es +0,011
y **no es estadísticamente significativa** (p=0,062), al precio de triplicar
la latencia. El criterio del objetivo específico 6 es el compromiso, no la
calidad máxima.

Y un resultado que conviene leer: **map-reduce no compra calidad**. Cuesta 10
invocaciones al modelo frente a 1, y la diferencia no es significativa.

---

## Cómo se garantiza que sirve lo que se midió

La aplicación **no reimplementa** la canalización: importa el paquete
`resumidor` del repositorio de investigación y llama a las mismas funciones
que ejecutaron el experimento (`construir_modelo`, `construir_estrategia`,
`ejecutar_documento`).

Hay un test que lo comprueba de verdad:

```bash
uv run pytest -m modelo
```

Toma un documento de la muestra experimental, lo resume con la web, y exige
que el texto sea **idéntico** al registrado en
`experiments/results/extractive_abstractive_bart.jsonl`. Si difiere, la
plataforma no está sirviendo la canalización evaluada.

---

## Desarrollo local

Requiere [uv](https://docs.astral.sh/uv/) y el repositorio de investigación
como carpeta hermana:

```
Udea/20262/pi1/
  repo/            ← carlosforeroudea/ps1-g2
  resumidor-web/   ← este repositorio
```

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8080
```

Abre <http://127.0.0.1:8080>. El primer arranque descarga ~1,5 GB de pesos.

```bash
uv run pytest -m "not modelo"   # rápido, sin descargas
uv run ruff check .
```

---

## Despliegue

Cloud Run en Google Cloud, proyecto `udea-509713`. Cada push a `main`
dispara la construcción de la imagen y el despliegue.

Se eligió Cloud Run tras comprobar que Hugging Face Spaces ya **no es
gratuito** para aplicaciones con cómputo: crear un Space de Gradio devuelve
`402 Payment Required` y exige suscripción PRO.

---

## Estructura

```
app/
  main.py        API HTTP (FastAPI)
  servicio.py    frontera con la canalización evaluada
  static/        interfaz
tests/
Dockerfile
```
