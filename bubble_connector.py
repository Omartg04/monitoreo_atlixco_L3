"""
Atlixco PIE L3 · bubble_connector.py
Capa de acceso a la Data API de Bubble. Solo descarga y caché — la limpieza y
las columnas derivadas viven en transform_atlixco_l3.py.

Decisiones de diseño, con su porqué:

  - Descarga EN SERIE. L2 (Zacatlán) paginaba en paralelo con 10 hilos porque
    manejaba ~29K registros. Aquí el universo es 800: son 8 páginas, unos pocos
    segundos. El paralelismo solo agregaría modos de falla.

  - Se pagina con `remaining`, NUNCA con `count`. Confirmado el 07/10/2026:
    la API reportó count=1 teniendo 2 registros en la tabla. `count` no es
    confiable para calcular cuántas páginas faltan.

  - Salvaguarda anti-degradación: Bubble nunca borra registros, así que el
    total jamás debe decrecer. Si una carga devuelve menos del SANITY_MARGIN
    de la última carga buena, se descarta la respuesta y se conserva el dato
    previo en vez de mostrar un avance falsamente bajo en pleno operativo.

  - El parámetro `fields=` (pedir solo los campos del FIELD_MAP para reducir
    payload y Workflow Units) queda disponible pero APAGADO por defecto: con
    137 campos la URL se va a ~1,800 caracteres y, a 800 registros, el ahorro
    no compensa el riesgo de topar un límite de longitud de URL.

Uso:
    from bubble_connector import descargar_crudo
    registros, info = descargar_crudo(private_key)
"""
from __future__ import annotations

import logging
import time

import requests

from config import (
    BUBBLE_ENDPOINT, BUBBLE_PAGE_SIZE, FIELD_MAP, SANITY_MARGIN,
)

logger = logging.getLogger(__name__)

TIMEOUT_SEG = 30
MAX_REINTENTOS = 3
USAR_PARAM_FIELDS = False   # ver nota de diseño arriba


def _pedir_pagina(endpoint: str, headers: dict, cursor: int) -> dict:
    """
    Una página, con back-off exponencial. Devuelve el cuerpo de 'response'.
    Lanza la excepción si agota los reintentos — quien llama decide si degrada.
    """
    params: dict = {"limit": BUBBLE_PAGE_SIZE, "cursor": cursor}
    if USAR_PARAM_FIELDS:
        params["fields"] = ",".join(FIELD_MAP.keys())

    for intento in range(MAX_REINTENTOS):
        try:
            resp = requests.get(endpoint, headers=headers, params=params,
                                timeout=TIMEOUT_SEG)
            resp.raise_for_status()
            return resp.json().get("response", {})
        except requests.RequestException as e:
            if intento == MAX_REINTENTOS - 1:
                logger.error("Bubble falló en cursor %d tras %d intentos: %s",
                             cursor, MAX_REINTENTOS, e)
                raise
            espera = 2 ** (intento + 1)
            logger.warning("Bubble error en cursor %d (intento %d): %s — "
                           "reintento en %ds", cursor, intento + 1, e, espera)
            time.sleep(espera)
    return {}


def descargar_crudo(private_key: str, endpoint: str = BUBBLE_ENDPOINT) -> list[dict]:
    """
    Descarga todos los registros del Thing 'Encuesta', sin transformar.
    Se detiene cuando `remaining` llega a 0 o una página vuelve vacía.
    """
    headers = {"Authorization": f"Bearer {private_key}"}
    registros: list[dict] = []
    cursor = 0

    while True:
        cuerpo = _pedir_pagina(endpoint, headers, cursor)
        lote = cuerpo.get("results", [])
        registros.extend(lote)
        restantes = cuerpo.get("remaining", 0)

        if restantes <= 0 or not lote:
            break
        cursor += BUBBLE_PAGE_SIZE

        # Cinturón de seguridad: a 800 registros esperados, pasar de 100 páginas
        # significa que `remaining` no está decreciendo y estaríamos en bucle.
        if cursor > BUBBLE_PAGE_SIZE * 100:
            logger.error("Paginación excedió 100 páginas — se corta por seguridad.")
            break

    logger.info("Bubble: %d registros descargados.", len(registros))
    return registros


def carga_degradada(n_nuevo: int, n_ultimo_bueno: int) -> bool:
    """
    True si la carga nueva luce incompleta frente a la última buena conocida.
    Bubble no borra registros: una caída del total siempre es error de la API,
    nunca un dato legítimo.
    """
    if n_ultimo_bueno <= 0:
        return False
    return n_nuevo < n_ultimo_bueno * SANITY_MARGIN
