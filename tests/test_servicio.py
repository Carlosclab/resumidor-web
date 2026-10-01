"""Tests de la capa de servicio. No descargan pesos.

El test que de verdad importa —que la web reproduzca exactamente el resumen
del experimento— está marcado `modelo` y se ejecuta a mano: descarga 1,5 GB
y tarda. Los de aquí protegen la frontera: validación, construcción del
documento y forma de la respuesta.
"""

from __future__ import annotations

import pytest

from app.servicio import (
    MAX_CARACTERES,
    MIN_CARACTERES,
    EntradaInvalida,
    Resumen,
    ruta_config,
    validar,
)

TEXTO_VALIDO = "the main properties of ultraluminous x-ray sources . " * 10


class TestValidar:
    def test_acepta_texto_normal(self) -> None:
        assert validar(TEXTO_VALIDO) == TEXTO_VALIDO.strip()

    def test_recorta_espacios(self) -> None:
        assert validar(f"\n\n  {TEXTO_VALIDO}  \n") == TEXTO_VALIDO.strip()

    @pytest.mark.parametrize("entrada", ["", "   ", "corto", None])
    def test_rechaza_texto_corto(self, entrada) -> None:
        # Gastar una invocación del modelo en 5 caracteres no tiene sentido.
        with pytest.raises(EntradaInvalida, match="demasiado corto"):
            validar(entrada)

    def test_rechaza_texto_enorme(self) -> None:
        # Sin tope, un pegado gigante deja al usuario esperando sin final.
        with pytest.raises(EntradaInvalida, match="supera el límite"):
            validar("x" * (MAX_CARACTERES + 1))

    def test_el_mensaje_de_error_es_util(self) -> None:
        """Va directo al usuario: debe decir qué pasa y qué hacer."""
        with pytest.raises(EntradaInvalida) as e:
            validar("hola")
        mensaje = str(e.value)
        assert str(MIN_CARACTERES) in mensaje
        assert "4" in mensaje  # cuántos caracteres recibió


class TestRutaConfig:
    def test_encuentra_la_configuracion_desplegada(self) -> None:
        assert ruta_config("extractive_abstractive_bart").is_file()

    def test_falla_con_mensaje_claro_si_no_existe(self) -> None:
        with pytest.raises(FileNotFoundError, match="no_existe"):
            ruta_config("no_existe")


class TestResumen:
    def test_traduce_el_resultado_de_la_canalizacion(self) -> None:
        """`Resumen` es lo único que cruza hacia la interfaz."""
        from resumidor.domain import CostMetrics, SummaryResult

        bruto = SummaryResult(
            summary="un resumen",
            cost=CostMetrics(
                latency_seconds=6.5432,
                peak_memory_mb=3148.3,
                model_calls=1,
                input_tokens=2002,
                output_tokens=154,
            ),
            model_name="facebook/bart-large-cnn",
            strategy_name="extractive_abstractive",
        )
        r = Resumen.desde(bruto)

        assert r.texto == "un resumen"
        assert r.latencia_s == 6.54  # redondeado para mostrar
        assert r.invocaciones == 1
        assert r.tokens_entrada == 2002
        assert 0 < r.ratio_compresion < 1


@pytest.mark.modelo
def test_reproduce_el_resultado_del_experimento() -> None:
    """La comprobación que sostiene el ADR-001.

    Si la web no devuelve exactamente el mismo resumen que el experimento
    para el mismo documento, no está sirviendo la canalización evaluada y la
    afirmación «la plataforma sirve la configuración que ganó» es falsa.

    Ejecutar con: pytest -m modelo
    """
    import json
    import pathlib

    from resumidor.config import cargar_config
    from resumidor.data.corpus import cargar_muestra

    from app.servicio import Resumidor

    resultados = pathlib.Path(
        "../repo/experiments/results/extractive_abstractive_bart.jsonl"
    )
    if not resultados.is_file():
        pytest.skip("no están los resultados del experimento")

    esperado = {
        json.loads(linea)["doc_id"]: json.loads(linea)
        for linea in resultados.read_text().splitlines()
        if linea.strip()
    }

    cfg = cargar_config(ruta_config("extractive_abstractive_bart"))
    documentos = cargar_muestra(
        dataset=cfg.sample.dataset,
        config=cfg.sample.config,
        split=cfg.sample.split,
        n=cfg.sample.n,
        seed=cfg.sample.seed,
        muestreo=cfg.sample.muestreo,
        estratos=cfg.sample.estratos,
    )
    documento = next(d for d in documentos if d.doc_id in esperado)

    servicio = Resumidor()
    servicio.precargar()
    obtenido = servicio.resumir(documento.text)

    assert obtenido.texto == esperado[documento.doc_id]["resumen_generado"]
    assert obtenido.tokens_entrada == esperado[documento.doc_id]["tokens_entrada"]
