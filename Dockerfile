# Imagen de la plataforma web, para Cloud Run.
#
# Distinta de la del repositorio de investigación: aquella es un job por lotes
# con ENTRYPOINT de script; esta es un servicio HTTP que escucha en $PORT.

FROM python:3.11-slim

# uv fijado por versión: si la imagen se reconstruye en seis meses, debe
# resolver el mismo árbol de dependencias.
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/modelos

# Torch de CPU explícito. La rueda por defecto de PyPI para linux arrastra
# las bibliotecas de CUDA (~2,5 GB) que en Cloud Run no sirven de nada: no
# hay GPU. La variante CPU son ~200 MB.
ENV UV_TORCH_BACKEND=cpu

# Dependencias en su propia capa: el código de la app cambia mucho más a
# menudo que ellas, y así la reconstrucción no repite la descarga de torch.
#
# `--no-sources` ignora [tool.uv.sources], que apunta a la carpeta hermana
# del repositorio de investigación y solo existe en la máquina del equipo.
# Aquí `resumidor` se instala desde git, que es la dependencia publicable.
COPY pyproject.toml README.md ./

# `git` es necesario para resolver la dependencia `resumidor`, que se declara
# por URL de git. La imagen base `slim` no lo trae. Se instala, se usa y se
# elimina en la MISMA capa: si se borrara en una capa posterior, los ~50 MB
# seguirían dentro de la imagen final.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && uv pip install --system --no-cache --no-sources --torch-backend=cpu . \
 && apt-get purge -y git \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY app/ app/

# Los pesos se hornean en la imagen, no se descargan al arrancar. Cloud Run
# tiene un tiempo límite de arranque y destruye el contenedor en reposo: sin
# esto, cada arranque en frío bajaría 1,5 GB y el primer usuario esperaría.
RUN python -c "\
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM; \
c='facebook/bart-large-cnn'; \
AutoTokenizer.from_pretrained(c); \
AutoModelForSeq2SeqLM.from_pretrained(c); \
print('pesos horneados en la imagen')"

# Verificación en tiempo de construcción: si falta un módulo o la
# configuración no se encuentra, el build falla aquí y no un servicio remoto.
RUN python -c "\
from app.servicio import ruta_config, Resumidor; \
print('configuración:', ruta_config('extractive_abstractive_bart')); \
Resumidor(); \
print('OK: la canalización se construye')"

EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
