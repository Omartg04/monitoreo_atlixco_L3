"""
Atlixco PIE L3 · transform_atlixco_l3.py
Ingesta desde Bubble + limpieza + columnas derivadas.

Es la única capa que conoce las rarezas del payload de Bubble. KPIs, flags y
dashboard consumen el DataFrame que sale de aquí y no vuelven a tocar la API.

Rarezas confirmadas contra registros reales (07/10/2026) y cómo se tratan:

  google_address        llega como OBJETO {address, lat, lng}, no string.
                        Sus lat/lng ya son float → fuente primaria de
                        coordenadas; 'latitud'/'longitud' (strings) son
                        respaldo.
  latitud / longitud    strings ("19.3658") → to_numeric.
  seccion_electoral     string, puede traer cero a la izquierda ("0164").
  codigo_*              MEZCLAN int y string en la misma batería (visto:
                        P15_6="1" junto a P15_1=1). Se normalizan TODOS a
                        numérico, si no el straightlining da falsos negativos.
  B_98 / B_98_texto     el rango de edad casi nunca llega: la pregunta solo
                        se dispara si el entrevistado no da edad exacta. El
                        bucket de cuota se deriva SIEMPRE de 'edad' numérica.
  P19_1_grabacion       URL relativa al protocolo ("//...") → se le antepone
                        "https:".
  (sin campo estatus)   'terminada' se deriva — ver CRITERIO_TERMINADA.

Manejo de zona horaria: las fechas se convierten a hora local de Ciudad de
México y se les quita el tzinfo. Streamlit Cloud corre en UTC; sin esta
conversión, "hoy" cambia de fecha seis horas antes de que en México sea el
día siguiente, y el panel del día amanece vacío. Todo el código aguas abajo
puede entonces tratar las fechas como hora local sin convertir de nuevo.

Uso:
    from transform_atlixco_l3 import get_encuestas
    df, ultima_act, info = get_encuestas(private_key)
"""
from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from bubble_connector import descargar_crudo, carga_degradada
from config import (
    FIELD_MAP, CACHE_TTL_SEC, CRITERIO_TERMINADA, DUR_MIN_TERMINADA,
    VERSIONES_CUESTIONARIO, CORRECCIONES_SECCION, REGISTROS_EXCLUIDOS,
    INICIO_OPERATIVO, FECHAS_EXCLUIDAS, APLICAR_CORTE_FECHA, HORA_INICIO_JORNADA,
    METAS_POR_SECCION, PUNTOS_POR_SECCION, RANGOS_EDAD,
    CANDIDATOS, ATRIBUTOS_BLOQUE7,
)

logger = logging.getLogger(__name__)

TZ_MEXICO = "America/Mexico_City"
MUNICIPIO_ESPERADO = "ATLIXCO"

_FECHA_INICIO = pd.Timestamp(INICIO_OPERATIVO).date()
_FECHAS_FUERA = {pd.Timestamp(f).date() for f in FECHAS_EXCLUIDAS}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def normalizar_nombre(valor) -> str:
    """
    Colapsa variantes de escritura del mismo nombre: espacios, acentos,
    mayúsculas. NO corrige typos reales — fusionar "ALEJANDRA" con
    "MALEJANDRA" automáticamente arriesga juntar a dos personas distintas.
    Esos casos van en CORRECCIONES_NOMBRE, uno por uno y confirmados.
    """
    if valor is None or str(valor).strip() in ("", "None", "nan"):
        return "SIN NOMBRE"
    s = str(valor).strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "SIN NOMBRE"


# Typos de captura confirmados uno por uno con coordinación de campo.
# Día 2: el mismo encuestador capturó su nombre de tres formas distintas,
# lo que infló el conteo de encuestadores y hundió sus métricas por persona.
CORRECCIONES_NOMBRE: dict[str, str] = {
    "EDUARDO": "EDUARDO CERVERA CEREZO",
    "EDUARDO CERVERA": "EDUARDO CERVERA CEREZO",
    "EDUARDO CEREZO": "EDUARDO CERVERA CEREZO",
}


def bin_edad_cuota(edad) -> str | None:
    """Mapea edad numérica al bucket de cuota. None si falta o está fuera de rango."""
    if pd.isna(edad):
        return None
    try:
        edad = int(edad)
    except (TypeError, ValueError):
        return None
    for lo, hi, etiqueta in RANGOS_EDAD:
        if lo <= edad <= hi:
            return etiqueta
    return None   # menores de 18 — revisar el dato, no debería haberlos


def bin_sexo_cuota(sexo) -> str | None:
    """Normaliza sexo a 'H'/'M'. Valor confirmado en Bubble: 'Masculino'/'Femenino'."""
    if not sexo:
        return None
    s = str(sexo).strip().upper()
    if s in ("H", "HOMBRE", "MASCULINO", "M_HOMBRE"):
        return "H"
    if s in ("M", "MUJER", "FEMENINO", "FEMENINA"):
        return "M"
    return None


def distancia_metros(lat1, lon1, lat2, lon2) -> float | None:
    """Haversine. Sin dependencias extra — a escala municipal el error es trivial."""
    if any(pd.isna(v) or v is None for v in (lat1, lon1, lat2, lon2)):
        return None
    R = 6_371_000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(a)), 1)


def _columnas_codigo() -> list[str]:
    """Columnas 'codigo_*' del FIELD_MAP — las que llevan escala de respuesta."""
    return [v for v in FIELD_MAP.values() if v.startswith("codigo_")]


def _llego_al_final(d: pd.DataFrame, ultima_pregunta: str) -> pd.Series:
    """
    ¿El entrevistado alcanzó `ultima_pregunta`? Es la base de `terminada`.

    Cuentan TAMBIÉN los códigos 97/98/99: un "No sabe" en la última pantalla
    significa que el entrevistado SÍ llegó ahí, y lo que mide completitud es
    haber recorrido la pantalla, no haber dado respuesta sustantiva. Es el
    único lugar del sistema donde la no-respuesta es señal positiva — en todo
    lo demás (promedios, straightlining) 97/98/99 se excluyen.
    """
    if ultima_pregunta == "P16":
        # Batería de 7 aspirantes, sin filtro: se pregunta a todos.
        cols = [f"codigo_votar_o_no_{cand}" for cand in CANDIDATOS
                if f"codigo_votar_o_no_{cand}" in d.columns]
        if not cols:
            logger.warning("No hay columnas de P16 — 'terminada' en False.")
            return pd.Series(False, index=d.index)
        return d[cols].notna().any(axis=1)

    if ultima_pregunta == "P19_1":
        if "preferencia_total_morena" not in d.columns:
            logger.warning("No hay columna de P19_1 — 'terminada' en False.")
            return pd.Series(False, index=d.index)
        return (d["preferencia_total_morena"].notna()
                & (d["preferencia_total_morena"].astype(str).str.strip() != ""))

    logger.warning("Última pregunta '%s' no reconocida — 'terminada' en False.",
                   ultima_pregunta)
    return pd.Series(False, index=d.index)


# ══════════════════════════════════════════════════════════════════════════════
# TRANSFORMACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def transformar(registros: list[dict]) -> pd.DataFrame:
    """Convierte el JSON crudo de Bubble en el DataFrame que consume la app."""
    columnas_base = list(FIELD_MAP.values()) + [
        "folio", "fecha", "duracion_min", "terminada", "estrato",
        "encuestador_id", "encuestador_nombre", "sexo_cuota", "rango_edad_cuota",
        "tiene_grabacion", "grabacion_duracion_seg", "grabacion_url",
        "punto_asignado", "distancia_punto_m", "version_cuestionario",
        "seccion_declarada", "fuente_seccion",
        "rescatable", "aprovechable",
    ]
    if not registros:
        return pd.DataFrame(columns=columnas_base)

    filas = []
    for r in registros:
        fila = {destino: r.get(llave) for llave, destino in FIELD_MAP.items()}

        # google_address es un objeto, no un string
        ga = r.get("google_address")
        if isinstance(ga, dict):
            fila["google_address"] = ga.get("address")
            fila["_ga_lat"] = ga.get("lat")
            fila["_ga_lng"] = ga.get("lng")
        else:
            fila["_ga_lat"] = None
            fila["_ga_lng"] = None

        filas.append(fila)

    df = pd.DataFrame(filas)

    # ── Fechas → hora local de México, sin tzinfo ─────────────────────────────
    for col in ("fecha_creacion", "fecha_modificacion"):
        df[col] = (
            pd.to_datetime(df[col], utc=True, errors="coerce")
            .dt.tz_convert(TZ_MEXICO)
            .dt.tz_localize(None)
        )

    df["fecha"] = df["fecha_creacion"].dt.date
    df["duracion_min"] = (
        (df["fecha_modificacion"] - df["fecha_creacion"])
        .dt.total_seconds().div(60).round(1)
    )

    # ── Corte del operativo ───────────────────────────────────────────────────
    # Se descarta por regla, no borrando en Bubble: así queda auditable qué
    # quedó fuera. En MODO="test" el corte no aplica (ver config.py).
    if APLICAR_CORTE_FECHA:
        antes = len(df)
        dentro = (
            df["fecha"].notna()
            & (df["fecha"] >= _FECHA_INICIO)
            & (~df["fecha"].isin(_FECHAS_FUERA))
        )
        # Corte por hora de arranque: fecha_creacion ya está en hora local de
        # México, así que la hora se compara directamente.
        if HORA_INICIO_JORNADA is not None:
            dentro &= (df["fecha_creacion"].dt.hour >= HORA_INICIO_JORNADA)

        df = df[dentro].copy()
        if antes != len(df):
            logger.info("Corte de operativo: %d de %d registros descartados "
                        "(previos al %s, en fechas excluidas, o antes de las "
                        "%s:00).", antes - len(df), antes, INICIO_OPERATIVO,
                        HORA_INICIO_JORNADA)

    if df.empty:
        return pd.DataFrame(columns=columnas_base)

    # ── Folio ─────────────────────────────────────────────────────────────────
    df["folio"] = df["id_unico"].astype(str)

    # ── Registros excluidos ───────────────────────────────────────────────────
    # Descartes individuales autorizados (ver registros_excluidos.csv): capturas
    # previas al arranque de la jornada, pruebas, registros invalidados en
    # auditoría. Se filtran aquí, después de asignar folio y antes de cualquier
    # cálculo, para que no entren a ningún KPI ni flag.
    if REGISTROS_EXCLUIDOS:
        fuera = df["folio"].isin(REGISTROS_EXCLUIDOS)
        if fuera.any():
            logger.info("Registros excluidos por autorización: %d (%s).",
                        int(fuera.sum()),
                        ", ".join(sorted(df.loc[fuera, "folio"])[:5]))
            df = df[~fuera].copy()
            if df.empty:
                return pd.DataFrame(columns=columnas_base)

    # ── Sección electoral ─────────────────────────────────────────────────────
    # Llega como string, puede traer cero a la izquierda. La sección 160 está
    # reseccionada (vigentes 3119-3123) pero campo captura 160 y el monitoreo
    # usa su polígono viejo — no hay remapeo que hacer.
    df["seccion_electoral"] = (
        pd.to_numeric(df["seccion_electoral"], errors="coerce").astype("Int64")
    )

    # ── Corrección de sección (incidente del día 1) ───────────────────────────
    # El 9 de octubre el catálogo del formulario estaba incompleto y la
    # instrucción fue declarar una sección sustituta. Las correcciones se
    # resolvieron una por una (GPS, manzana de muestra y documentación de
    # coordinación) y viven en correcciones_seccion_dia1.csv.
    #
    # Se conserva lo capturado en `seccion_declarada` y se sobrescribe
    # `seccion_electoral` con la corregida: así KPIs, flags y dashboard siguen
    # consumiendo "la sección de la encuesta" sin cambiar, y el dato original
    # queda disponible para auditoría.
    df["seccion_declarada"] = df["seccion_electoral"]
    df["fuente_seccion"] = "declarada"

    if CORRECCIONES_SECCION:
        corregidas = df["folio"].map(
            lambda f: CORRECCIONES_SECCION.get(f, (None, None))[0])
        fuentes = df["folio"].map(
            lambda f: CORRECCIONES_SECCION.get(f, (None, None))[1])
        hay = corregidas.notna()
        if hay.any():
            df.loc[hay, "seccion_electoral"] = (
                pd.to_numeric(corregidas[hay], errors="coerce").astype("Int64"))
            df.loc[hay, "fuente_seccion"] = fuentes[hay]
            logger.info("Secciones corregidas: %d de %d registros.",
                        int(hay.sum()), len(df))

    # ── Coordenadas ───────────────────────────────────────────────────────────
    # google_address.lat/lng ya son float y son la fuente primaria.
    # latitud/longitud llegan como string y sirven de respaldo.
    for destino, respaldo, principal in (("latitud", "latitud", "_ga_lat"),
                                         ("longitud", "longitud", "_ga_lng")):
        serie_respaldo = pd.to_numeric(df[respaldo], errors="coerce")
        serie_principal = pd.to_numeric(df[principal], errors="coerce")
        df[destino] = serie_principal.fillna(serie_respaldo)
    df.drop(columns=["_ga_lat", "_ga_lng"], inplace=True, errors="ignore")

    # ── Escalas de respuesta a numérico ───────────────────────────────────────
    # CRÍTICO: Bubble mezcla int y string dentro de la misma batería
    # (P15_6="1" junto a P15_1=1). Sin esta normalización, nunique() ve dos
    # valores distintos donde hay uno solo y el straightlining no dispara.
    for col in _columnas_codigo():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["edad"] = pd.to_numeric(df["edad"], errors="coerce").astype("Int64")

    # Desfragmentar antes de añadir las ~15 columnas derivadas que siguen. El
    # bucle de arriba reasigna las 137 columnas de código una por una, lo que
    # fragmenta el DataFrame; sin este copy(), cada columna nueva dispara un
    # PerformanceWarning de pandas y llena los logs de Streamlit Cloud.
    df = df.copy()

    # ── Buckets de cuota ──────────────────────────────────────────────────────
    # El bucket SIEMPRE sale de 'edad' numérica. 'rango_edad' (B_98_texto) casi
    # nunca llega y es poco confiable — mismo hallazgo que en Zacatlán.
    df["sexo_cuota"] = df["sexo"].apply(bin_sexo_cuota)
    df["rango_edad_cuota"] = df["edad"].apply(bin_edad_cuota)

    # ── Encuestador ───────────────────────────────────────────────────────────
    # 'Created By' es el ID interno del usuario de Bubble: identificador estable
    # aunque escriban el nombre distinto entre capturas.
    df["encuestador_nombre"] = df["nombre_encuestador"].apply(normalizar_nombre)
    df["encuestador_nombre"] = df["encuestador_nombre"].replace(CORRECCIONES_NOMBRE)
    df["encuestador_id"] = (
        df["user_creador"].astype(str).str.strip()
        .replace({"": None, "None": None, "nan": None})
        .fillna(df["encuestador_nombre"])
    )

    # ── Municipio ─────────────────────────────────────────────────────────────
    df["municipio_texto"] = df["municipio_texto"].astype(str).str.strip().str.upper()

    # ── Grabación (P19_1) ─────────────────────────────────────────────────────
    df["grabacion_duracion_seg"] = pd.to_numeric(
        df.get("duracion_grabacion_pregunta_p19_1"), errors="coerce"
    )
    url_grab = df.get("grabacion_pregunta_p19_1")
    if url_grab is not None:
        # La URL llega relativa al protocolo ("//...cdn.bubble.io/...").
        df["grabacion_url"] = (
            url_grab.astype(str)
            .where(url_grab.notna(), None)
            .str.replace(r"^//", "https://", regex=True)
        )
    else:
        df["grabacion_url"] = None
    df["tiene_grabacion"] = df["grabacion_url"].notna() & (df["grabacion_url"] != "None")

    # ── Versión del cuestionario ──────────────────────────────────────────────
    # El orden de pantallas cambió el 10 de octubre: P19_1 pasó de la 15 a la 8
    # y P16 quedó como última sustantiva. Se marca la versión para que el
    # análisis pueda separar ambas mediciones de P19_1, que no son comparables
    # (en v1 se preguntaba después de las baterías de atributos, en v2 antes).
    df["version_cuestionario"] = "v1"
    for etiqueta, info in VERSIONES_CUESTIONARIO.items():
        desde = pd.Timestamp(info["desde"]).date()
        hasta = pd.Timestamp(info["hasta"]).date() if info["hasta"] else None
        dentro = df["fecha"].notna() & (df["fecha"] >= desde)
        if hasta is not None:
            dentro &= df["fecha"] <= hasta
        df.loc[dentro, "version_cuestionario"] = etiqueta

    # ── Terminada ─────────────────────────────────────────────────────────────
    # Bubble no expone estatus. Ver CRITERIO_TERMINADA en config.py.
    if CRITERIO_TERMINADA == "por_version":
        # Cada registro se evalúa con la última pregunta de SU versión del
        # cuestionario: la v1 (9 oct) terminaba en P19_1, la v2 en P16. Medir
        # ambas con la misma regla contaría mal una de las dos.
        df["terminada"] = False
        for etiqueta, info in VERSIONES_CUESTIONARIO.items():
            de_esta_version = df["version_cuestionario"] == etiqueta
            if not de_esta_version.any():
                continue
            df.loc[de_esta_version, "terminada"] = _llego_al_final(
                df.loc[de_esta_version], info["ultima_pregunta"]
            )

    elif CRITERIO_TERMINADA == "p16":
        # P16 (votaría / nunca votaría) es la última pregunta sustantiva desde
        # la v2 del cuestionario, y se pregunta a TODOS sin filtro.
        #
        # Cuentan también los códigos 98/99: un "No sabe" ahí significa que el
        # entrevistado SÍ recorrió la pantalla, que es lo que mide completitud.
        # Por eso se usa notna() sobre la columna cruda y NO se filtran los
        # códigos de no-respuesta, al contrario del resto del sistema.
        cols_p16 = [f"codigo_votar_o_no_{cand}" for cand in CANDIDATOS
                    if f"codigo_votar_o_no_{cand}" in df.columns]
        if cols_p16:
            df["terminada"] = df[cols_p16].notna().any(axis=1)
        else:
            logger.warning("No hay columnas de P16 en el DataFrame — "
                           "'terminada' queda en False para todos.")
            df["terminada"] = False

    elif CRITERIO_TERMINADA == "pregunta_final":
        # Criterio de la v1: llegó a P19_1 (entonces en la pantalla 15).
        # ⚠️ Inválido desde la v2: P19_1 está en la pantalla 8, así que una
        #    encuesta abandonada a media aplicación lo cumpliría.
        df["terminada"] = df["preferencia_total_morena"].notna() & (
            df["preferencia_total_morena"].astype(str).str.strip() != ""
        )
    else:
        df["terminada"] = df["duracion_min"].fillna(0) >= DUR_MIN_TERMINADA

    # ── Rescatable y aprovechable ─────────────────────────────────────────────
    # Una entrevista abandonada no es necesariamente pérdida. Si alcanzó a
    # registrar perfil y la preferencia de P19.1, sirve para la pregunta
    # principal aunque le falte el cierre.
    #
    # El perfil es requisito, no un nivel más: sin sexo ni rango de edad el
    # caso no se puede ponderar, por sustanciosa que sea la respuesta.
    #
    # P19.1 se da por registrada con el texto capturado O con el audio: en la
    # práctica viajan juntos, y exigir ambos descartaría casos válidos.
    #
    # 'terminada' NO cambia de significado. Mide lo que siempre midió —llegar
    # a la última pregunta— y los flags de calidad siguen colgando de ella.
    # 'aprovechable' es la que se compara contra la meta.
    _perfil = df["sexo_cuota"].notna() & df["rango_edad_cuota"].notna()

    _p19_texto = pd.Series(False, index=df.index)
    for _col in ("preferencia_total_morena", "codigo_preferencia_total_morena"):
        if _col in df.columns:
            _vals = df[_col]
            _p19_texto |= _vals.notna() & (
                _vals.astype(str).str.strip().isin(["", "None", "nan", "<NA>"]) == False
            )

    _p19_audio = (df["tiene_grabacion"].fillna(False)
                  if "tiene_grabacion" in df.columns
                  else pd.Series(False, index=df.index))

    df["rescatable"] = (~df["terminada"]) & _perfil & (_p19_texto | _p19_audio)
    df["aprovechable"] = df["terminada"] | df["rescatable"]

    # ── Estrato (desde la muestra) ────────────────────────────────────────────
    _estrato = {sec: v["estrato"] for sec, v in METAS_POR_SECCION.items()}
    df["estrato"] = df["seccion_electoral"].map(_estrato).fillna("Fuera de muestra")

    # ── Punto de manzana asignado más cercano ─────────────────────────────────
    df[["punto_asignado", "distancia_punto_m"]] = df.apply(
        _punto_mas_cercano, axis=1, result_type="expand"
    )

    return df.reset_index(drop=True)


def _punto_mas_cercano(fila: pd.Series) -> tuple[str | None, float | None]:
    """
    Manzana objetivo más cercana DENTRO de la sección declarada, con su
    distancia en metros. Compara contra los puntos de esa sección, no contra
    los 200: una encuesta puede caer cerca de una manzana de otra sección y
    aun así estar mal ubicada respecto a la suya.
    """
    sec = fila.get("seccion_electoral")
    lat, lon = fila.get("latitud"), fila.get("longitud")
    if pd.isna(sec) or pd.isna(lat) or pd.isna(lon):
        return None, None

    puntos = PUNTOS_POR_SECCION.get(int(sec), [])
    if not puntos:
        return None, None

    mejor_id, mejor_dist = None, None
    for p in puntos:
        d = distancia_metros(lat, lon, p["latitud"], p["longitud"])
        if d is not None and (mejor_dist is None or d < mejor_dist):
            mejor_id, mejor_dist = p["punto_id"], d
    return mejor_id, mejor_dist


# ══════════════════════════════════════════════════════════════════════════════
# CARGA CON CACHÉ
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def _cargar(private_key: str) -> tuple[pd.DataFrame, float]:
    """Caché global, compartido entre sesiones: un ciclo de refresh por TTL."""
    registros = descargar_crudo(private_key)
    return transformar(registros), time.time()


def get_encuestas(
    private_key: str,
    secciones: list[int] | None = None,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, datetime | None, dict]:
    """
    Retorna (df, ultima_actualizacion, info).
    info = {"degradado": bool, "mensaje": str | None}

    Si la API falla o devuelve una carga sospechosamente incompleta, se
    conserva el último dato confiable de la sesión en vez de mostrar un avance
    falsamente bajo — en pleno operativo eso dispara decisiones equivocadas.
    """
    if force_refresh:
        _cargar.clear()

    info = {"degradado": False, "mensaje": None}

    try:
        df, ts = _cargar(private_key)
    except Exception as e:
        logger.warning("Error cargando desde Bubble: %s", e)
        df_previo = st.session_state.get("_ultimo_df_bueno")
        if df_previo is not None:
            return (
                _filtrar(df_previo, secciones),
                _a_datetime(st.session_state.get("_ultimo_ts_bueno")),
                {"degradado": True,
                 "mensaje": "No se pudo conectar con Bubble. "
                            "Mostrando el último dato confiable."},
            )
        return (
            transformar([]), None,
            {"degradado": True,
             "mensaje": "No se pudo conectar con Bubble y no hay datos previos "
                        "en esta sesión."},
        )

    n_ultimo = st.session_state.get("_ultimo_conteo_bueno", 0)
    if carga_degradada(len(df), n_ultimo):
        logger.warning("Carga degradada: %d registros vs %d de la última buena.",
                       len(df), n_ultimo)
        _cargar.clear()
        df_previo = st.session_state.get("_ultimo_df_bueno")
        info = {
            "degradado": True,
            "mensaje": (f"Bubble devolvió una respuesta incompleta ({len(df)} de "
                        f"~{n_ultimo} registros esperados). Mostrando el último "
                        f"dato confiable."),
        }
        if df_previo is not None:
            return (_filtrar(df_previo, secciones),
                    _a_datetime(st.session_state.get("_ultimo_ts_bueno")), info)
    else:
        st.session_state["_ultimo_df_bueno"] = df
        st.session_state["_ultimo_conteo_bueno"] = len(df)
        st.session_state["_ultimo_ts_bueno"] = ts

    return _filtrar(df, secciones), _a_datetime(ts), info


def _filtrar(df: pd.DataFrame, secciones: list[int] | None) -> pd.DataFrame:
    """Filtro por lista de secciones permitidas (rol del usuario). En memoria."""
    if not secciones or df.empty:
        return df
    return df[df["seccion_electoral"].isin(secciones)].copy()


def _a_datetime(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# PRUEBA DESDE TERMINAL
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import os
    import types

    # Mock mínimo de Streamlit para correr fuera de la app.
    _almacen: dict = {}

    def _mock_cache(ttl=None, show_spinner=True):
        def decorador(fn):
            def envoltura(*args, **kwargs):
                clave = repr(args) + repr(sorted(kwargs.items()))
                if clave not in _almacen:
                    _almacen[clave] = fn(*args, **kwargs)
                return _almacen[clave]
            envoltura.clear = _almacen.clear
            envoltura.__wrapped__ = fn
            return envoltura
        return decorador

    import transform_atlixco_l3 as t
    t.st = types.SimpleNamespace(cache_data=_mock_cache, session_state={})
    t._cargar = _mock_cache()(t._cargar.__wrapped__)

    key = os.getenv("BUBBLE_PRIVATE_KEY", "")
    if not key:
        raise SystemExit('export BUBBLE_PRIVATE_KEY="tu_key" antes de correr.')

    df, ts, info = t.get_encuestas(key)
    print(f"Registros        : {len(df)}")
    if info["degradado"]:
        print(f"⚠️  {info['mensaje']}")
    if df.empty:
        raise SystemExit("Sin registros dentro del corte del operativo.")

    print(f"Terminadas       : {int(df['terminada'].sum())} "
          f"(criterio: {CRITERIO_TERMINADA})")
    print(f"Secciones        : {sorted(df['seccion_electoral'].dropna().unique().tolist())}")
    print(f"Encuestadores    : {df['encuestador_nombre'].nunique()}")
    print(f"Con coordenadas  : {int(df['latitud'].notna().sum())}")
    print(f"Con grabación    : {int(df['tiene_grabacion'].sum())}")
    print(f"Última act.      : {ts}")
    print(f"\nDuración (min):\n{df['duracion_min'].describe()}")
    print(f"\nDistancia al punto asignado (m):\n{df['distancia_punto_m'].describe()}")
    print(f"\nCuotas (sexo x edad):\n"
          f"{pd.crosstab(df['rango_edad_cuota'], df['sexo_cuota'], dropna=False)}")
