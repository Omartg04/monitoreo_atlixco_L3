"""
Atlixco PIE · config.py — Encuesta L3 (tercera ola)
Parámetros centralizados del visualizador de monitoreo y auditoría.
Editar AQUÍ — no tocar app.py / kpis.py / flags.py para cambiar umbrales o mapeos.

Fuentes:
  - Diccionario_Referencia_Atlixco_version_lite_2 (137 campos)
  - SiteMap_Encuesta_aspirante_morena_Atlixco_version_lite_2 (17 pantallas)
  - Atlixco_maestra_L3.xlsx · hoja Muestra_200 (200 manzanas, 45 secciones, n=800)
  - secciones_atlixco.geojson (heredado de L2 — cubre las 45 secciones del L3)

Diferencias de fondo vs L2 — NO es un L2 recortado:
  - 7 aspirantes (se suman Lupita Muciño y Benjamin Minutti), no 5
  - 6 atributos por aspirante (desaparece derecho_mujeres); el bloque de
    conocimiento del estado usa P13 — P12 NO existe en L3
  - Desaparecen P1/P2/P3 (dirección del estado, problema, inseguridad,
    servicios), P20/P21 (careos), P22 (aprobación de autoridades) y atl_*
  - Sin agrupación por zonas: las secciones se monitorean planas
  - Nueva pregunta con grabación de audio (P19_1) + duración en segundos
  - Cuotas: grid sexo x 4 rangos de edad (L2 era solo H/M)
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent

# ══════════════════════════════════════════════════════════════════════════════
# MODO DE OPERACIÓN
# ══════════════════════════════════════════════════════════════════════════════
# "produccion" → endpoint de producción + corte por fecha de operativo activo.
# "test"       → endpoint version-test + SIN corte por fecha (para ver capturas
#                de prueba durante el armado). Nunca dejar "test" en el deploy.
#
# Decidido 07/10/2026: el operativo corre en PRODUCCIÓN y la captura válida
# empieza el viernes 9 de octubre. Las capturas previas (pruebas de armado y
# capacitación del jueves 8) se descartan por regla aquí, no borrándolas en
# Bubble — así queda auditable qué se excluyó y por qué.
MODO = "produccion"          # "produccion" | "test"

_BUBBLE_HOST = "https://encuesta-aspirante-atlixco-vl2.bubbleapps.io"
_BUBBLE_RUTA = {
    "produccion": "/api/1.1",
    "test":       "/version-test/api/1.1",
}[MODO]

BUBBLE_BASE_URL  = f"{_BUBBLE_HOST}{_BUBBLE_RUTA}"
BUBBLE_ENDPOINT  = f"{BUBBLE_BASE_URL}/obj/Encuesta"   # Thing type: "Encuesta"
BUBBLE_WF_URL    = f"{BUBBLE_BASE_URL}/wf"
BUBBLE_PAGE_SIZE = 100
CACHE_TTL_SEC    = 1800      # 30 min — operativo de 2 días, refresh frecuente
AUTO_REFRESH_SEC = 300

# La private key NO vive aquí. Se lee de .streamlit/secrets.toml:
#   [bubble]
#   private_key = "..."
# Producción y version-test comparten la misma key; solo cambia la ruta.

# ══════════════════════════════════════════════════════════════════════════════
# CALENDARIO DEL OPERATIVO
# ══════════════════════════════════════════════════════════════════════════════
INICIO_OPERATIVO = "2026-10-09"          # viernes — primer día de captura válida

# Fechas cuyas capturas se descartan aunque caigan dentro del rango.
FECHAS_EXCLUIDAS = {
    "2026-10-08",                        # jueves — capacitación de encuestadores
}

# En MODO="test" se ignoran ambos cortes para poder ver las capturas de armado.
APLICAR_CORTE_FECHA = (MODO == "produccion")

# ══════════════════════════════════════════════════════════════════════════════
# ASPIRANTES Y ATRIBUTOS
# ══════════════════════════════════════════════════════════════════════════════
# El sufijo numérico de Bubble (P8_1 ... P8_7) sigue este mismo orden.
CANDIDATOS = {
    "arturo":    "Arturo Solano Escobedo",
    "ana":       "Ana Laura Altamirano",
    "modesta":   "Modesta Delgado",
    "francisco": "Francisco García",
    "jesus":     "Jesús Luévano",
    "lupita":    "Lupita Muciño",
    "benjamin":  "Benjamin Minutti",
}

# 6 atributos (L2 tenía 7 — se cae derecho_mujeres).
# OJO con la numeración de Bubble: no es correlativa.
ATRIBUTOS_BLOQUE7 = [
    "honestidad",           # P10
    "cercania",             # P11
    "conocimiento_estado",  # P13  ← P12 no existe en L3
    "cumplimiento",         # P14
    "buena_candidatura",    # P15
    "votar_o_no",           # P16
]

# ══════════════════════════════════════════════════════════════════════════════
# MAPEO CAMPOS BUBBLE → APP
# ══════════════════════════════════════════════════════════════════════════════
# Generado desde el diccionario de referencia y VALIDADO contra registros
# reales de Bubble el 07/10/2026 (diagnostico_bubble_L3.py).
#
# Correcciones que salieron de esa validación:
#   - 'P9_5_text' SÍ va sin la "o" (confirmado en el JSON crudo).
#   - 'P9_7_text' NO existe: Bubble devuelve 'P9_7_texto'. El diccionario
#     estaba mal. Corregido abajo.
#   - 'B_98' / 'B_98_texto' (rango de edad) no llegan: la pregunta solo se
#     dispara cuando el entrevistado no da edad exacta. Se dejan mapeados por
#     si aparecen, pero el bucket de cuota SIEMPRE se deriva de 'edad'
#     numérica — nunca de 'rango_edad'. Mismo comportamiento que en Zacatlán.
#
# Comportamientos de la API confirmados, a manejar en la transformación:
#   - Bubble OMITE del JSON los campos sin valor; un campo ausente no implica
#     mapeo incorrecto. Usar .get() en todos lados.
#   - 'google_address' llega como OBJETO {address, lat, lng}, no como string.
#   - 'latitud' / 'longitud' llegan como STRING ("19.3658"); convertir a float.
#   - 'seccion_electoral' llega como STRING y puede traer cero a la izquierda
#     ("0164"); normalizar con to_numeric.
#   - Los campos 'codigo_*' mezclan int y string en la MISMA batería (visto:
#     P15_6 = "1" mientras P15_1..P15_5 y P15_7 = 1). Hay que normalizarlos a
#     numérico antes de evaluar straightlining, o la comparación de valores
#     idénticos da falsos negativos.
#   - 'P19_1_grabacion' es una URL relativa al protocolo ("//...cdn.bubble.io/
#     ..."); anteponer "https:" para que sea abrible.
#   - 'P19_1_grabacion_duracion' llega como int (segundos).
#   - El 'count' que reporta la API no es confiable (devolvió 1 con 2 registros
#     en la tabla). Paginar siempre con 'remaining', nunca con 'count'.

FIELD_MAP = {
    # 01-iniciar-encuesta
    '_id'                     : 'id_unico',
    'Created Date'            : 'fecha_creacion',
    'Modified Date'           : 'fecha_modificacion',
    'Created By'              : 'user_creador',
    'nombre_encuestador'      : 'nombre_encuestador',
    'seccion_electoral'       : 'seccion_electoral',
    'municipio_texto'         : 'municipio_texto',
    # 02-ubicacion-googlemaps
    'google_address'          : 'google_address',
    'latitud'                 : 'latitud',
    'longitud'                : 'longitud',
    # 04-genero-edad
    'A'                       : 'codigo_sexo',
    'A_texto'                 : 'sexo',
    'B'                       : 'edad',
    'B_98'                    : 'codigo_rango_edad',
    'B_98_texto'              : 'rango_edad',
    'C'                       : 'codigo_ine',
    'C_texto'                 : 'ine',
    # 05-intencion-voto
    'P4'                      : 'codigo_identificacion_partidaria',
    'P4_texto'                : 'identificacion_partidaria',
    # 06-eleccion-gobernador
    'P5'                      : 'codigo_intencion_voto_principal',
    'P5_texto'                : 'intencion_voto_principal',
    # 07-conocimiento-candidato
    'P8_1'                    : 'codigo_conocimiento_arturo',
    'P8_1_texto'              : 'conocimiento_arturo',
    'P8_2'                    : 'codigo_conocimiento_ana',
    'P8_2_texto'              : 'conocimiento_ana',
    'P8_3'                    : 'codigo_conocimiento_modesta',
    'P8_3_texto'              : 'conocimiento_modesta',
    'P8_4'                    : 'codigo_conocimiento_francisco',
    'P8_4_texto'              : 'conocimiento_francisco',
    'P8_5'                    : 'codigo_conocimiento_jesus',
    'P8_5_texto'              : 'conocimiento_jesus',
    'P8_6'                    : 'codigo_conocimiento_lupita',
    'P8_6_texto'              : 'conocimiento_lupita',
    'P8_7'                    : 'codigo_conocimiento_benjamin',
    'P8_7_texto'              : 'conocimiento_benjamin',
    # 08-opinion-candidato
    'P9_1'                    : 'codigo_opinion_arturo',
    'P9_1_texto'              : 'opinion_arturo',
    'P9_2'                    : 'codigo_opinion_ana',
    'P9_2_texto'              : 'opinion_ana',
    'P9_3'                    : 'codigo_opinion_modesta',
    'P9_3_texto'              : 'opinion_modesta',
    'P9_4'                    : 'codigo_opinion_francisco',
    'P9_4_texto'              : 'opinion_francisco',
    'P9_5'                    : 'codigo_opinion_jesus',
    'P9_5_text'               : 'opinion_jesus',
    'P9_6'                    : 'codigo_opinion_lupita',
    'P9_6_texto'              : 'opinion_lupita',
    'P9_7'                    : 'codigo_opinion_benjamin',
    'P9_7_texto'              : 'opinion_benjamin',   # el diccionario decía 'P9_7_text' — Bubble devuelve '_texto'
    # 09-atributo-honestidad
    'P10_1'                   : 'codigo_honestidad_arturo',
    'P10_1_texto'             : 'honestidad_arturo',
    'P10_2'                   : 'codigo_honestidad_ana',
    'P10_2_texto'             : 'honestidad_ana',
    'P10_3'                   : 'codigo_honestidad_modesta',
    'P10_3_texto'             : 'honestidad_modesta',
    'P10_4'                   : 'codigo_honestidad_francisco',
    'P10_4_texto'             : 'honestidad_francisco',
    'P10_5'                   : 'codigo_honestidad_jesus',
    'P10_5_texto'             : 'honestidad_jesus',
    'P10_6'                   : 'codigo_honestidad_lupita',
    'P10_6_texto'             : 'honestidad_lupita',
    'P10_7'                   : 'codigo_honestidad_benjamin',
    'P10_7_texto'             : 'honestidad_benjamin',
    # 10-atributo-cercania
    'P11_1'                   : 'codigo_cercania_arturo',
    'P11_1_texto'             : 'cercania_arturo',
    'P11_2'                   : 'codigo_cercania_ana',
    'P11_2_texto'             : 'cercania_ana',
    'P11_3'                   : 'codigo_cercania_modesta',
    'P11_3_texto'             : 'cercania_modesta',
    'P11_4'                   : 'codigo_cercania_francisco',
    'P11_4_texto'             : 'cercania_francisco',
    'P11_5'                   : 'codigo_cercania_jesus',
    'P11_5_texto'             : 'cercania_jesus',
    'P11_6'                   : 'codigo_cercania_lupita',
    'P11_6_texto'             : 'cercania_lupita',
    'P11_7'                   : 'codigo_cercania_benjamin',
    'P11_7_texto'             : 'cercania_benjamin',
    # 11-atributo-conocimiento
    'P13_1'                   : 'codigo_conocimiento_estado_arturo',
    'P13_1_texto'             : 'conocimiento_estado_arturo',
    'P13_2'                   : 'codigo_conocimiento_estado_ana',
    'P13_2_texto'             : 'conocimiento_estado_ana',
    'P13_3'                   : 'codigo_conocimiento_estado_modesta',
    'P13_3_texto'             : 'conocimiento_estado_modesta',
    'P13_4'                   : 'codigo_conocimiento_estado_francisco',
    'P13_4_texto'             : 'conocimiento_estado_francisco',
    'P13_5'                   : 'codigo_conocimiento_estado_jesus',
    'P13_5_texto'             : 'conocimiento_estado_jesus',
    'P13_6'                   : 'codigo_conocimiento_estado_lupita',
    'P13_6_texto'             : 'conocimiento_estado_lupita',
    'P13_7'                   : 'codigo_conocimiento_estado_benjamin',
    'P13_7_texto'             : 'conocimiento_estado_benjamin',
    # 12-atributo-cumplimiento
    'P14_1'                   : 'codigo_cumplimiento_arturo',
    'P14_1_texto'             : 'cumplimiento_arturo',
    'P14_2'                   : 'codigo_cumplimiento_ana',
    'P14_2_texto'             : 'cumplimiento_ana',
    'P14_3'                   : 'codigo_cumplimiento_modesta',
    'P14_3_texto'             : 'cumplimiento_modesta',
    'P14_4'                   : 'codigo_cumplimiento_francisco',
    'P14_4_texto'             : 'cumplimiento_francisco',
    'P14_5'                   : 'codigo_cumplimiento_jesus',
    'P14_5_texto'             : 'cumplimiento_jesus',
    'P14_6'                   : 'codigo_cumplimiento_lupita',
    'P14_6_texto'             : 'cumplimiento_lupita',
    'P14_7'                   : 'codigo_cumplimiento_benjamin',
    'P14_7_texto'             : 'cumplimiento_benjamin',
    # 13-atributo-buen-candidato
    'P15_1'                   : 'codigo_buena_candidatura_arturo',
    'P15_1_texto'             : 'buena_candidatura_arturo',
    'P15_2'                   : 'codigo_buena_candidatura_ana',
    'P15_2_texto'             : 'buena_candidatura_ana',
    'P15_3'                   : 'codigo_buena_candidatura_modesta',
    'P15_3_texto'             : 'buena_candidatura_modesta',
    'P15_4'                   : 'codigo_buena_candidatura_francisco',
    'P15_4_texto'             : 'buena_candidatura_francisco',
    'P15_5'                   : 'codigo_buena_candidatura_jesus',
    'P15_5_texto'             : 'buena_candidatura_jesus',
    'P15_6'                   : 'codigo_buena_candidatura_lupita',
    'P15_6_texto'             : 'buena_candidatura_lupita',
    'P15_7'                   : 'codigo_buena_candidatura_benjamin',
    'P15_7_texto'             : 'buena_candidatura_benjamin',
    # 14-atributos-voto
    'P16_1'                   : 'codigo_votar_o_no_arturo',
    'P16_1_texto'             : 'votar_o_no_arturo',
    'P16_2'                   : 'codigo_votar_o_no_ana',
    'P16_2_texto'             : 'votar_o_no_ana',
    'P16_3'                   : 'codigo_votar_o_no_modesta',
    'P16_3_texto'             : 'votar_o_no_modesta',
    'P16_4'                   : 'codigo_votar_o_no_francisco',
    'P16_4_texto'             : 'votar_o_no_francisco',
    'P16_5'                   : 'codigo_votar_o_no_jesus',
    'P16_5_texto'             : 'votar_o_no_jesus',
    'P16_6'                   : 'codigo_votar_o_no_lupita',
    'P16_6_texto'             : 'votar_o_no_lupita',
    'P16_7'                   : 'codigo_votar_o_no_benjamin',
    'P16_7_texto'             : 'votar_o_no_benjamin',
    # 15-candidatura-morena
    'P19_1'                   : 'codigo_preferencia_total_morena',
    'P19_1_texto'             : 'preferencia_total_morena',
    'P19_1_grabacion'         : 'grabacion_pregunta_p19_1',
    'P19_1_grabacion_duracion': 'duracion_grabacion_pregunta_p19_1',
}

# ══════════════════════════════════════════════════════════════════════════════
# THINGS RELACIONADOS — PENDIENTE DE DEFINIR ALCANCE
# ══════════════════════════════════════════════════════════════════════════════
# El registro real trae tres campos de LISTA que no están en el diccionario ni
# en el sitemap. Cada uno es una lista de IDs de otro Thing de Bubble, es decir
# preguntas que SÍ se están capturando y que hoy no entran al dashboard:
#
#   candidatos              7 ids  — uno por aspirante
#   evaluaciones_autoridad  3 ids  — probablemente el bloque de aprobación
#                                    (alcaldesa / gobernador / presidenta),
#                                    equivalente a P22_1/2/3 en L2
#   medios                  9 ids  — bloque de medios de comunicación, sin
#                                    equivalente en L2
#
# Resolverlos exige una segunda llamada a la Data API por Thing type. Mientras
# no se defina el alcance, se ignoran: no afectan metas, avance ni flags.
# Ver diagnostico_things_relacionados.py para descubrir sus Thing types reales.
THINGS_RELACIONADOS = ["candidatos", "evaluaciones_autoridad", "medios"]
RESOLVER_THINGS_RELACIONADOS = False     # ← activar cuando se defina el alcance

# ── Campos que no se muestran en el dashboard ─────────────────────────────────
# Se conservan en el DataFrame: lat/lon alimentan los flags de georreferencia,
# user_creador alimenta encuestador_id.
CAMPOS_OCULTAR_DISPLAY = {
    "google_address", "latitud", "longitud", "user_creador",
    "grabacion_pregunta_p19_1",
}
CAMPOS_EXCLUIR = set()   # sin PII de nombre/celular/email en la versión lite

# ══════════════════════════════════════════════════════════════════════════════
# UMBRALES DE CALIDAD (FLAGS)
# ══════════════════════════════════════════════════════════════════════════════
# TODO CONFIRMAR con stakeholder tras el primer día de campo. Los valores de
# arranque vienen de L2 (cuestionario de tamaño comparable: 137 vs 162 campos,
# pero con 7 aspirantes en vez de 5, así que el bloque de atributos es más largo).

# — Duración de entrevista —
# Decidido 07/10/2026: rojo por debajo de 4 min. Con 7 aspirantes x 6 atributos
# (42 ítems) más el bloque 6, una entrevista real no baja de ahí.
DUR_MIN_MIN = 4      # por debajo → rojo
DUR_MAX_MIN = 40     # por encima → rojo
# Zona de alerta temprana: entre DUR_MIN_MIN y DUR_MIN_MIN × este factor.
DUR_FACTOR_AMARILLO = 1.2

# — Racha de capturas rápidas —
# Patrón adoptado del conector de inducción: una encuesta corta aislada es
# plausible; N seguidas del mismo encuestador el mismo día, no. Es mejor señal
# que el tiempo absoluto de un registro suelto.
UMBRAL_DURACION_RACHA_MIN = 3.0   # minutos
MIN_RACHA_RAPIDA          = 3     # encuestas consecutivas bajo el umbral

# — Horario operativo —
HORA_INICIO_OPERATIVO = 8    # 08:00 hrs, hora local Ciudad de México
HORA_FIN_OPERATIVO    = 19   # 19:00 hrs

# — Grabación de audio (P19_1) —
# Bubble expone dos campos: el mp3 y su duración en segundos.
GRABACION_DURACION_MIN_SEG = 5    # grabación más corta que esto → amarillo
                                  # grabación ausente                → rojo

# — Distancia al punto de manzana asignado —
# Decidido 07/10/2026: la distancia a la manzana NO genera flag. Hay secciones
# donde la manzana seleccionada tiene pocas viviendas y el encuestador tiene
# que moverse legítimamente; marcarlo en rojo produciría ruido, no señal.
# La georreferencia se evalúa a nivel SECCIÓN (flag_georef_fuera_zona).
# La distancia se conserva como columna de diagnóstico: se muestra en la tabla
# y en el mapa para que el supervisor vea dispersión, sin disparar alertas.
FLAG_DISTANCIA_MANZANA_ACTIVO = False
DIST_MANZANA_REFERENCIA_M = 500   # solo para colorear el mapa, no para flags

# ══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE CÓDIGOS (Codigos_bloques.docx, 07/10/2026)
# ══════════════════════════════════════════════════════════════════════════════
# Los códigos 97/98/99 NO son valores de escala: son no-respuesta. Hay que
# excluirlos de TODO promedio, comparación y batería de straightlining. Si se
# cuelan, un "No sabe" codificado 98 se lee como la peor calificación posible
# y una batería de puros 99 dispara straightlining siendo en realidad una
# no-respuesta.
CODIGOS_NO_SUSTANTIVOS = {97, 98, 99}   # 97 Ninguno · 98 No sabe · 99 No respondió

# Escalas por pregunta. En TODAS, el código 1 es la respuesta más favorable.
ESCALAS = {
    "conocimiento":        {"min": 1, "max": 2},   # 1 Sí · 2 No
    "opinion":             {"min": 1, "max": 3},   # 1 Buena · 2 Regular · 3 Mala
    "honestidad":          {"min": 1, "max": 4},   # 1 Mucho · 2 Algo · 3 Poco · 4 Nada
    "cercania":            {"min": 1, "max": 4},
    "conocimiento_estado": {"min": 1, "max": 4},
    "cumplimiento":        {"min": 1, "max": 4},
    "buena_candidatura":   {"min": 1, "max": 2},   # 1 Sí · 2 No
    "votar_o_no":          {"min": 1, "max": 2},   # 1 Votaría · 2 Nunca votaría
}

# ⚠️  'conocimiento_estado' está MAL NOMBRADO: el nombre viene de L2, donde la
#     pregunta era sobre el estado para una elección de gubernatura. En L3 la
#     pregunta literal es "¿Qué tanto considera que (...) conoce el MUNICIPIO
#     DE ATLIXCO?". La variable conserva el nombre para no romper el mapeo,
#     pero en el dashboard DEBE etiquetarse como "Conoce el municipio".
ETIQUETAS_ATRIBUTO = {
    "honestidad":          "Honestidad",
    "cercania":            "Cercanía con la gente",
    "conocimiento_estado": "Conoce el municipio",   # NO "conoce el estado"
    "cumplimiento":        "Cumple lo que dice",
    "buena_candidatura":   "Es buen(a) candidato(a)",
    "votar_o_no":          "Votaría / nunca votaría",
}

# Preguntas con filtro en el instrumento (no se preguntan a todos). Su ausencia
# es ESTRUCTURAL, no no-respuesta: no se puede interpretar un nulo como mala
# evaluación ni computarlo en un promedio.
#   conocimiento (P8)        → a todos
#   opinion (P9)             → solo a quien respondió "Sí conoce"
#   honestidad, cercania,
#   conocimiento_estado,
#   cumplimiento             → solo a quien respondió "Sí conoce"
#   buena_candidatura (P15)  → solo a quien dio opinión "Buena"  ← filtro doble
#   votar_o_no (P16)         → a todos
ATRIBUTOS_FILTRO_CONOCE = [
    "honestidad", "cercania", "conocimiento_estado", "cumplimiento",
]
ATRIBUTOS_FILTRO_OPINION_BUENA = ["buena_candidatura"]
ATRIBUTOS_SIN_FILTRO = ["votar_o_no"]

# Los 4 atributos con filtro y escala comunes (1-4, condicionados a "sí conoce")
# son la única base comparable entre aspirantes. buena_candidatura queda fuera
# de cualquier comparación: su filtro doble la vuelve una muestra autoseleccionada.
ATRIBUTOS_COMPARABLES = ATRIBUTOS_FILTRO_CONOCE

# ── Baterías candidatas a straightlining ──────────────────────────────────────
# ⚠️  Solo entran baterías cuyos ítems comparten la MISMA escala y tienen más
#     de dos opciones. Dos razones, ambas descubiertas al probar contra datos:
#
#   1. Mezclar escalas invalida la comparación. L2 metía los 7 atributos en una
#      sola batería, pero honestidad/cercanía/conoce-municipio/cumple van en
#      escala 1-4 mientras buena_candidatura y votar_o_no son binarias. Un "1"
#      en una y un "1" en otra no son "la misma respuesta": significan cosas
#      distintas. Comparar sus códigos es comparar manzanas con naranjas.
#
#   2. Una batería binaria no distingue pereza de respuesta legítima. Contestar
#      "Sí conozco" a los 7 aspirantes es una respuesta normal y frecuente, no
#      straightlining. Marcarla llenaría el tablero de falsos positivos y
#      entrenaría al supervisor a ignorar las alertas.
#
# Queda entonces:
#   bloque7_<aspirante>  4 ítems, escala 1-4 (los atributos comparables)
#   bloque6_opinion      7 ítems, escala 1-3 (una opinión por aspirante)
# Se evalúan sobre las columnas 'codigo_*', nunca sobre el texto.
BATERIAS_STRAIGHTLINING = {
    **{
        f"bloque7_{cand}": [f"codigo_{atrib}_{cand}" for atrib in ATRIBUTOS_COMPARABLES]
        for cand in CANDIDATOS
    },
    "bloque6_opinion": [f"codigo_opinion_{cand}" for cand in CANDIDATOS],
}

# ══════════════════════════════════════════════════════════════════════════════
# DETECCIÓN DE SESGO HACIA EL ASPIRANTE FOCO
# ══════════════════════════════════════════════════════════════════════════════
# El patrón a detectar es el formulario rellenado con respuestas favorables al
# aspirante del cliente. Tres piezas, de menos a más accionable:
#
#   score_favorabilidad_<foco>  columna continua, sin alerta
#   flag_sesgo_<foco>           registro, umbral extremo
#   flag_sesgo_encuestador      agregado por encuestador  ← el que sirve
#
# Nota metodológica: a nivel registro, un simpatizante real produce el mismo
# patrón que un encuestador que rellena. Por eso el flag de registro usa un
# corte casi imposible en la vida real, y el juicio operativo se hace a nivel
# encuestador, comparando contra sus propios compañeros en el mismo municipio.
CANDIDATO_FOCO = "arturo"        # GENERAL ARTURO SOLANO

# Flag de registro: dispara solo con el patrón extremo — el foco en el valor
# MÁS favorable de los 4 atributos comparables Y "votaría", mientras TODOS los
# demás aspirantes conocidos quedan en el valor MENOS favorable Y "nunca votaría".
SESGO_MIN_OTROS_CONOCIDOS = 3    # mínimo de otros aspirantes conocidos para evaluar

# Flag de encuestador: tasa de registros pro-foco que se desvía de la tasa
# global del operativo. Se compara contra el resto del equipo, no contra una
# expectativa externa — si Atlixco entero favorece al foco, eso sube la tasa
# global y nadie se marca por ello.
SESGO_ENC_MIN_ENCUESTAS   = 10   # no evaluar encuestadores con pocos registros
SESGO_ENC_DESVIO_AMARILLO = 1.5  # desviaciones estándar sobre la tasa global
SESGO_ENC_DESVIO_ROJO     = 2.5

# — Criterio de "encuesta terminada" —
# Bubble no expone ningún campo de estatus (confirmado 07/10/2026), así que
# hay que derivarlo. Dos criterios posibles:
#
#   "pregunta_final"  La encuesta llegó a P19_1 (preferencia MORENA), que es
#                     la última pregunta sustantiva del instrumento: pantalla
#                     15 de 17, solo seguida de la despedida. Si está
#                     contestada, el cuestionario se recorrió completo.
#   "duracion"        duracion_min >= DUR_MIN_TERMINADA. Es el criterio que se
#                     usó en L2, por falta de algo mejor. Es más débil: la
#                     duración sale de Modified − Created, y cualquier edición
#                     posterior del registro la infla retroactivamente.
#
# Se usa "pregunta_final" por defecto: mide completitud real en vez de tiempo
# transcurrido. ⚠️ CONFIRMAR con campo que P19_1 no es salteable.
CRITERIO_TERMINADA = "pregunta_final"    # "pregunta_final" | "duracion"
DUR_MIN_TERMINADA  = 1.0                 # minutos, solo si CRITERIO = "duracion"

# — Meta diaria por encuestador —
META_ENCUESTAS_DIARIAS = 20   # TODO: confirmar con coordinación de campo

# — Salvaguarda anti-degradación de la API —
# Bubble nunca borra registros: si una carga devuelve menos del X% de la última
# carga buena, la respuesta se descarta y se conserva el dato previo.
SANITY_MARGIN = 0.90

# ══════════════════════════════════════════════════════════════════════════════
# IDENTIDAD VISUAL
# ══════════════════════════════════════════════════════════════════════════════
VERDE    = "#2E7D5E"
VERDE_L  = "#52B788"
AZUL     = "#1A3A5C"
AZUL_L   = "#2C6E9E"
NARANJA  = "#E07B39"
ROJO     = "#C0392B"
AMARILLO = "#F5A623"
GRIS_BG  = "#F2F4F7"

ESTRATO_COLORES = {
    "Urbana": AZUL_L,
    "Rural":  VERDE,
    "Mixta":  NARANJA,
}

# ══════════════════════════════════════════════════════════════════════════════
# GEOGRAFÍA
# ══════════════════════════════════════════════════════════════════════════════
ENTIDAD           = 21     # Puebla (catálogo IEE/INE)
CLAVE_MUNICIPIO   = 19     # Atlixco
GEOJSON_SECCIONES = "secciones_atlixco.geojson"   # heredado de L2 — 70 secciones
ATLIXCO_CENTRO    = [18.9010, -98.4506]
ATLIXCO_ZOOM      = 12

# Sección 160: reseccionada en 2026 (vigentes 3119-3123). Decisión 07/10/2026:
# el encuestador captura 160 y el monitoreo usa el POLÍGONO VIEJO de la 160,
# que cubre el área completa de las cinco secciones nuevas. No se regenera el
# GeoJSON. Son 13 manzanas y 52 entrevistas (6.5% de la muestra).
SECCIONES_RESECCIONADAS = {160: [3119, 3120, 3121, 3122, 3123]}

# ══════════════════════════════════════════════════════════════════════════════
# MUESTRA Y METAS
# ══════════════════════════════════════════════════════════════════════════════
# 45 secciones · 200 manzanas · 4 entrevistas por manzana · n = 800
#   estrato        Urbana (29 secciones, 512) / Rural (8, 148) / Mixta (8, 140)
#   ln_mos         suma del listado nominal muestreado en etapa 2
#   fexp_prom      factor de expansión acumulado promedio — se guarda para el
#                  análisis expost; el dashboard no lo usa

METAS_POR_SECCION = {
     154: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  198, 'fexp_prom': 12.9633, 'observaciones': None},
     158: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  126, 'fexp_prom': 35.3943, 'observaciones': None},
     159: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  309, 'fexp_prom': 25.7048, 'observaciones': None},
     160: {'estrato': 'Urbana',   'manzanas': 13, 'localidades': 1, 'meta_encuestas': 52, 'ln_mos':    0, 'fexp_prom': 20.8462, 'observaciones': 'reseccionada 2026 (3119-3123) — se captura y monitorea como 160'},
     161: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  285, 'fexp_prom': 8.5542, 'observaciones': None},
     164: {'estrato': 'Urbana',   'manzanas':  5, 'localidades': 1, 'meta_encuestas': 20, 'ln_mos':  536, 'fexp_prom': 9.0287, 'observaciones': None},
     165: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  389, 'fexp_prom': 8.5477, 'observaciones': None},
     166: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  405, 'fexp_prom': 7.7519, 'observaciones': None},
     168: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  514, 'fexp_prom': 7.0826, 'observaciones': None},
     169: {'estrato': 'Urbana',   'manzanas':  5, 'localidades': 1, 'meta_encuestas': 20, 'ln_mos':  423, 'fexp_prom': 20.0334, 'observaciones': None},
     170: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  283, 'fexp_prom': 10.9069, 'observaciones': None},
     173: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  556, 'fexp_prom': 4.4111, 'observaciones': None},
     174: {'estrato': 'Mixta',    'manzanas':  6, 'localidades': 2, 'meta_encuestas': 24, 'ln_mos':  370, 'fexp_prom': 14.5104, 'observaciones': None},
     175: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  455, 'fexp_prom': 7.2623, 'observaciones': None},
     176: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  257, 'fexp_prom': 10.2061, 'observaciones': None},
     178: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  398, 'fexp_prom': 9.3876, 'observaciones': None},
     182: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  231, 'fexp_prom': 10.3674, 'observaciones': None},
     185: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  389, 'fexp_prom': 9.5518, 'observaciones': None},
     186: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  454, 'fexp_prom': 5.9233, 'observaciones': None},
     188: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  349, 'fexp_prom': 7.4287, 'observaciones': None},
     190: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  428, 'fexp_prom': 6.0145, 'observaciones': None},
     192: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  618, 'fexp_prom': 4.0196, 'observaciones': None},
     194: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  276, 'fexp_prom': 43.6457, 'observaciones': None},
     196: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  165, 'fexp_prom': 21.4304, 'observaciones': None},
     198: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos': 1168, 'fexp_prom': 2.1171, 'observaciones': None},
     200: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  196, 'fexp_prom': 17.0306, 'observaciones': None},
     201: {'estrato': 'Urbana',   'manzanas':  5, 'localidades': 1, 'meta_encuestas': 20, 'ln_mos':  246, 'fexp_prom': 15.8033, 'observaciones': None},
     202: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  153, 'fexp_prom': 17.0058, 'observaciones': None},
     203: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  195, 'fexp_prom': 16.4904, 'observaciones': None},
     204: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  124, 'fexp_prom': 24.9362, 'observaciones': None},
     206: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  158, 'fexp_prom': 16.0982, 'observaciones': None},
     207: {'estrato': 'Mixta',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  117, 'fexp_prom': 149.4698, 'observaciones': None},
     208: {'estrato': 'Rural',    'manzanas':  9, 'localidades': 3, 'meta_encuestas': 36, 'ln_mos':  574, 'fexp_prom': 23.1555, 'observaciones': None},
     209: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 3, 'meta_encuestas': 16, 'ln_mos':  134, 'fexp_prom': 21.4613, 'observaciones': None},
     212: {'estrato': 'Mixta',    'manzanas':  5, 'localidades': 2, 'meta_encuestas': 20, 'ln_mos':  314, 'fexp_prom': 33.8323, 'observaciones': None},
     213: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  222, 'fexp_prom': 15.8941, 'observaciones': None},
     214: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  164, 'fexp_prom': 14.8331, 'observaciones': None},
     215: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  219, 'fexp_prom': 16.3360, 'observaciones': None},
     216: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  146, 'fexp_prom': 35.0977, 'observaciones': None},
     217: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 2, 'meta_encuestas': 16, 'ln_mos':  165, 'fexp_prom': 22.0527, 'observaciones': None},
     218: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  194, 'fexp_prom': 11.7507, 'observaciones': None},
     220: {'estrato': 'Rural',    'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':   97, 'fexp_prom': 40.8633, 'observaciones': None},
    2874: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  269, 'fexp_prom': 11.9823, 'observaciones': None},
    2875: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  247, 'fexp_prom': 10.2986, 'observaciones': None},
    2876: {'estrato': 'Urbana',   'manzanas':  4, 'localidades': 1, 'meta_encuestas': 16, 'ln_mos':  221, 'fexp_prom': 11.2227, 'observaciones': None},
}

SECCIONES_MUESTRA = [154, 158, 159, 160, 161, 164, 165, 166, 168, 169, 170, 173, 174, 175, 176, 178, 182, 185, 186, 188, 190, 192, 194, 196, 198, 200, 201, 202, 203, 204, 206, 207, 208, 209, 212, 213, 214, 215, 216, 217, 218, 220, 2874, 2875, 2876]

SECCIONES_FUERA_MUESTRA = [155, 156, 157, 162, 167, 171, 172, 177, 179, 180, 181, 183, 184, 187, 189, 191, 193, 195, 197, 199, 205, 210, 211, 219, 221]

META_GLOBAL = sum(v["meta_encuestas"] for v in METAS_POR_SECCION.values())
assert META_GLOBAL == 800, f"Meta global inesperada: {META_GLOBAL}"

META_POR_ESTRATO: dict[str, int] = {}
SECCIONES_POR_ESTRATO: dict[str, list[int]] = {}
for _sec, _v in METAS_POR_SECCION.items():
    _e = _v["estrato"]
    META_POR_ESTRATO[_e] = META_POR_ESTRATO.get(_e, 0) + _v["meta_encuestas"]
    SECCIONES_POR_ESTRATO.setdefault(_e, []).append(_sec)
for _e in SECCIONES_POR_ESTRATO:
    SECCIONES_POR_ESTRATO[_e].sort()

ESTRATOS = sorted(SECCIONES_POR_ESTRATO.keys())

# ══════════════════════════════════════════════════════════════════════════════
# PUNTOS DE MUESTRA (200 manzanas con coordenadas)
# ══════════════════════════════════════════════════════════════════════════════
# Se cargan de puntos_muestra_L3.csv (generado desde Atlixco_maestra_L3.xlsx)
# en vez de embeberse aquí: actualizar la muestra es reemplazar un archivo, no
# editar config. La carga es defensiva — si falta el CSV la app sigue viva y
# solo se desactivan el mapa de puntos y el flag de distancia.
ARCHIVO_PUNTOS = "puntos_muestra_L3.csv"

# ── Mapas de campo compartibles ───────────────────────────────────────────────
# Mapas de Google Maps hechos para que el brigadista los abra en su celular SIN
# entrar al tablero. El coordinador —que sí tiene acceso— los copia del tab de
# Planeación y los manda por WhatsApp.
#
# Cada entrada: una etiqueta visible, la URL del mapa, y las secciones que cubre
# (lista vacía = aplica a todo el operativo). La estructura admite un mapa por
# sección, por brigada o por grupo de secciones indistintamente: lo que decide
# qué se muestra es la lista `secciones`.
#
# Cargados desde Lista_Mapas_Digitales_-_Atlixco.xlsx (07/10/2026): 45 mapas,
# uno por sección. Las URLs se guardan en forma de VISTA (/viewer?mid=...),
# no de edición: el Excel las traía como /u/0/edit?mid=...&usp=sharing, que
# ancla el enlace a la primera cuenta de Google con sesión en el dispositivo
# y abre el mapa en modo edición. El enlace de edición se conserva solo en el
# Excel original, no se distribuye a campo.
# ⚠️  Que el enlace sea de vista NO basta: hay que verificar en Google My Maps
#     que cada mapa esté compartido como "cualquier persona con el enlace
#     puede VER". Si está como "puede editar", cualquiera que lo reciba
#     puede mover geometrías.
MAPAS_CAMPO: list[dict] = [
    {"etiqueta": 'Sección 154 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1EJklY8HwXDRuDJ1toG7pZMY2MMegQBg", "secciones": [154]},
    {"etiqueta": 'Sección 158 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1r5k1gfrqXZCDs5L4_ip3zFHMpubCAD4", "secciones": [158]},
    {"etiqueta": 'Sección 159 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1mJdJJyNMufJjgRF3j0qPgRJxWsUNf2g", "secciones": [159]},
    {"etiqueta": 'Sección 160R · Urbana',             "url": "https://www.google.com/maps/d/viewer?mid=1EQEsp1XhfNgJ0hgkm0ITQ4jmUrz8bis", "secciones": [160]},
    {"etiqueta": 'Sección 161 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1Oci4Io7vWWLfUD9VViyWbTZxFfti3C4", "secciones": [161]},
    {"etiqueta": 'Sección 164 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1cTwLskIuuk-FwFoJoMt7lRTnghELHlA", "secciones": [164]},
    {"etiqueta": 'Sección 165 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=12bM67F9sMiV7nbotbf3ADo4lru1qCok", "secciones": [165]},
    {"etiqueta": 'Sección 166 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1RQcELv2VUHxlgaNSzrgoedMs9BLkKYE", "secciones": [166]},
    {"etiqueta": 'Sección 168 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1HB3vr-exYjNxGP-zU9ko6BWFx3OaP84", "secciones": [168]},
    {"etiqueta": 'Sección 169 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1r-jRC1WBj5xHJWNuveuNZifTCSSMviA", "secciones": [169]},
    {"etiqueta": 'Sección 170 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1XM3PDnrmivm3BPGK6DABp0FHfG_Zs00", "secciones": [170]},
    {"etiqueta": 'Sección 173 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1JQ9Dau0E8NYo1kUH3pD_FRYyvuYfuuE", "secciones": [173]},
    {"etiqueta": 'Sección 174 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1FgLZbpk6yDo70QIP9wZC_V-DtUtkVX8", "secciones": [174]},
    {"etiqueta": 'Sección 175 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1VUpGXGrmk010GIkw1ht8Z8YtsbQgu1o", "secciones": [175]},
    {"etiqueta": 'Sección 176 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1FMJx28E4Wf33whA9nkvTLWU8kmb_OrY", "secciones": [176]},
    {"etiqueta": 'Sección 178 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1F-Y8NUABAAQklfon_402e6Wxg7UjJ_k", "secciones": [178]},
    {"etiqueta": 'Sección 182 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1bGHCQ4c-Y6tkfHPRsurcAcjpkm_COWw", "secciones": [182]},
    {"etiqueta": 'Sección 185 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1vVpSRZRKU8X4zXXHMV8wTFLXI4o0B8M", "secciones": [185]},
    {"etiqueta": 'Sección 186 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1eqZ6XFe0YROUvGXonq8qB3zowhDzmEk", "secciones": [186]},
    {"etiqueta": 'Sección 188 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1KFj11FrXTYY8za-UZPzCXKWA_JwOx1s", "secciones": [188]},
    {"etiqueta": 'Sección 190 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1f9P6UaBDyNuCxfmXcjUHbXukzbMUPn0", "secciones": [190]},
    {"etiqueta": 'Sección 192 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1Vu9tjBx-PS3OhXzHHKOBm_ltv0e_1QQ", "secciones": [192]},
    {"etiqueta": 'Sección 194 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1Tu9_Ilqqlj0x21i223BkxkCoX8RbIDw", "secciones": [194]},
    {"etiqueta": 'Sección 196 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1xQJ35i7LdVOaZrUd9a1f7p0xOCSUJo8", "secciones": [196]},
    {"etiqueta": 'Sección 198 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=19R19O-JYWtymULRpevfIpzH8PoLtMpo", "secciones": [198]},
    {"etiqueta": 'Sección 200 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1qtGlETGWkuxO0Z_xZrulLO_O-SGJ87k", "secciones": [200]},
    {"etiqueta": 'Sección 201 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1YjuuxbPAkJjeb3aK6eHNmPcXQSvcej8", "secciones": [201]},
    {"etiqueta": 'Sección 202 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1dsQl8xA2i2jQOQ0OyFb-jLvMV3ZELe0", "secciones": [202]},
    {"etiqueta": 'Sección 203 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1khTHWOWOvxrAVuXD1UnFD4nTLbxVz8E", "secciones": [203]},
    {"etiqueta": 'Sección 204 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1QeeQmo0Qv1unBmLl5kgmHrCv_WCj-gY", "secciones": [204]},
    {"etiqueta": 'Sección 206 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1t5L5-PBd-bfrK81Dyjtev_gErtrnd64", "secciones": [206]},
    {"etiqueta": 'Sección 207 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1mqOlSqbhzjcZFsVd36dbWhfeSZJPIFY", "secciones": [207]},
    {"etiqueta": 'Sección 208 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1VHODZvBH5uyZFFuUdq3gHQDNr5NGTB8", "secciones": [208]},
    {"etiqueta": 'Sección 209 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1Jb7S9RoT6IHViGSJcZnrxnv8e6yXBQ8", "secciones": [209]},
    {"etiqueta": 'Sección 212 · Mixta',               "url": "https://www.google.com/maps/d/viewer?mid=1CfO-NIw-VX0Dsi1TV6QbERu6I9Gw4vo", "secciones": [212]},
    {"etiqueta": 'Sección 213 · Urbana',              "url": "https://www.google.com/maps/d/viewer?mid=1We4TfV_uB72apQry7TyNt-SRtGp7Zco", "secciones": [213]},
    {"etiqueta": 'Sección 214 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1lV93UYHTM7ekeeXgyV8ho5j7EgzpFkc", "secciones": [214]},
    {"etiqueta": 'Sección 215 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1b7mQAEYro0htmhb9kEoUeiKuk5ISA68", "secciones": [215]},
    {"etiqueta": 'Sección 216 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1vCZxBTCJEFIX3uAEXmP-5wLjz8yI7TM", "secciones": [216]},
    {"etiqueta": 'Sección 217 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1K-tjEwCQuHO5gllYwkgesKF_eWGvleo", "secciones": [217]},
    {"etiqueta": 'Sección 218 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1aE2gOUJrr0iq4YpqGCGwncmHqZsanfY", "secciones": [218]},
    {"etiqueta": 'Sección 220 · Rural',               "url": "https://www.google.com/maps/d/viewer?mid=1sgxuwMp1O92UEbG36e-c7c3xzo_dBEU", "secciones": [220]},
    {"etiqueta": 'Sección 2874 · Urbana',             "url": "https://www.google.com/maps/d/viewer?mid=1jRPfdoNsAdZUwuBSIdU5eF1mx0AFeMk", "secciones": [2874]},
    {"etiqueta": 'Sección 2875 · Urbana',             "url": "https://www.google.com/maps/d/viewer?mid=1yfLT7NrlkMTac7nRebaR8LTcT5UCnbw", "secciones": [2875]},
    {"etiqueta": 'Sección 2876 · Urbana',             "url": "https://www.google.com/maps/d/viewer?mid=16ff-z2FtKt_novG0kzR3WhDRb2zRxTY", "secciones": [2876]},
]

_NUM_PUNTO = {"seccion", "localidad", "manzana", "meta_punto"}
_FLT_PUNTO = {"latitud", "longitud", "ln_mos", "fexp"}


def _cargar_puntos_muestra(nombre: str = ARCHIVO_PUNTOS) -> list[dict]:
    ruta = _BASE_DIR / nombre
    if not ruta.exists():
        logger.warning(
            "No se encontró %s — se desactivan el mapa de manzanas y el flag "
            "de distancia al punto asignado.", ruta
        )
        return []
    puntos: list[dict] = []
    with open(ruta, encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            registro: dict = {}
            for clave, valor in fila.items():
                if clave in _NUM_PUNTO:
                    registro[clave] = int(float(valor)) if valor not in ("", None) else None
                elif clave in _FLT_PUNTO:
                    registro[clave] = float(valor) if valor not in ("", None) else None
                else:
                    registro[clave] = valor
            puntos.append(registro)
    return puntos


PUNTOS_MUESTRA = _cargar_puntos_muestra()

# Índice sección → puntos, para el flag de distancia y el mapa de planeación.
PUNTOS_POR_SECCION: dict[int, list[dict]] = {}
for _p in PUNTOS_MUESTRA:
    PUNTOS_POR_SECCION.setdefault(_p["seccion"], []).append(_p)

# ══════════════════════════════════════════════════════════════════════════════
# CUOTAS DEMOGRÁFICAS
# ══════════════════════════════════════════════════════════════════════════════
# Grid de 8 celdas: sexo (H/M) × 4 rangos de edad.
#
# ⚠️  LAS PROPORCIONES DE ABAJO SON PLACEHOLDER — se calibran expost.
#     Son una estructura de población adulta aproximada, NO una cuota
#     derivada del marco muestral de Atlixco. Recalibrar = cambiar estos 8
#     números; las metas por sección se recalculan solas.
RANGOS_EDAD = [
    (18, 29, "18-29"),
    (30, 44, "30-44"),
    (45, 59, "45-59"),
    (60, 200, "60+"),
]
ETIQUETAS_RANGO_EDAD = [etiqueta for _, _, etiqueta in RANGOS_EDAD]
SEXOS_CUOTA = ["H", "M"]

DISTRIBUCION_CUOTAS: dict[tuple[str, str], float] = {
    ("18-29", "H"): 0.130, ("18-29", "M"): 0.140,
    ("30-44", "H"): 0.140, ("30-44", "M"): 0.150,
    ("45-59", "H"): 0.115, ("45-59", "M"): 0.125,
    ("60+",   "H"): 0.095, ("60+",   "M"): 0.105,
}
assert abs(sum(DISTRIBUCION_CUOTAS.values()) - 1.0) < 1e-9, "Las cuotas deben sumar 1.0"


def _repartir_mayores_restos(total: int, pesos: dict) -> dict:
    """
    Reparte `total` entre las celdas de `pesos` de modo que las partes sumen
    EXACTAMENTE `total`. Se asigna la parte entera y los enteros sobrantes van
    a las celdas con mayor residuo decimal (método de mayores restos).
    Evita el error típico de redondear cada celda por separado y terminar con
    una cuota que no cuadra contra la meta de la sección.
    """
    exactos = {celda: total * peso for celda, peso in pesos.items()}
    base    = {celda: int(valor) for celda, valor in exactos.items()}
    faltan  = total - sum(base.values())
    orden   = sorted(exactos, key=lambda c: exactos[c] - base[c], reverse=True)
    for celda in orden[:faltan]:
        base[celda] += 1
    return base


def cuotas_de_seccion(seccion: int) -> dict[str, dict[str, int]]:
    """
    Cuota objetivo de una sección como {rango_edad: {"H": n, "M": n}}.
    Siempre suma meta_encuestas de esa sección.
    """
    meta    = METAS_POR_SECCION[seccion]["meta_encuestas"]
    reparto = _repartir_mayores_restos(meta, DISTRIBUCION_CUOTAS)
    salida: dict[str, dict[str, int]] = {r: {} for r in ETIQUETAS_RANGO_EDAD}
    for (rango, sexo), n in reparto.items():
        salida[rango][sexo] = n
    return salida


# Cuota global (las 45 secciones). Se calcula sobre META_GLOBAL en vez de sumar
# las cuotas por sección: el redondeo por sección introduce desviaciones de
# ±1 celda que al agregarse no reflejarían la distribución objetivo real.
CUOTAS_GLOBALES = _repartir_mayores_restos(META_GLOBAL, DISTRIBUCION_CUOTAS)

# ══════════════════════════════════════════════════════════════════════════════
# ROLES (streamlit-authenticator 0.3.3 — pinear la versión en requirements.txt)
# ══════════════════════════════════════════════════════════════════════════════
# Sin zonas en L3: todos los supervisores ven las 45 secciones. Si más adelante
# hace falta restringir por brigada, agregar entradas con "rol": "parcial" y la
# lista de secciones correspondiente.
# ⚠️  secrets.toml: sin caracteres especiales en nombres de grupo; clave HMAC
#     de al menos 32 bytes.
_SECCIONES_TODAS = list(METAS_POR_SECCION.keys())

ROLES = {
    "yarith":  {"rol": "estatal", "secciones": _SECCIONES_TODAS},
    "data_ai": {"rol": "estatal", "secciones": _SECCIONES_TODAS},
}