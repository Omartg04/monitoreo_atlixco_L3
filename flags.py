"""
Atlixco PIE L3 · flags.py
Sistema de flags de calidad y sesgo.

Cada flag_* recibe el DataFrame ya transformado (transform_atlixco_l3.py) y
devuelve el mismo DataFrame con columnas añadidas:
    flag_<nombre>        bool
    flag_<nombre>_nivel  'rojo' | 'amarillo' | None

Flags de nivel registro:
  1. flag_duracion                    tiempos de entrevista anómalos
  2. flag_straightlining              baterías con respuesta idéntica
  3. flag_georef_fuera_zona           coordenadas fuera de la sección declarada
  4. flag_horario                     levantamiento fuera del horario operativo
  5. flag_grabacion                   grabación ausente o demasiado corta
  6. flag_inconsistencia_conocimiento Bloque 6 vs Bloque 7 por aspirante
  7. flag_sesgo_foco                  patrón extremo pro-aspirante foco

Flags de nivel agregado (devuelven otro shape, se llaman por separado):
  8. flag_racha_rapida                rachas de capturas rápidas por encuestador
  9. flag_sesgo_encuestador           tasa pro-foco desviada del promedio global
 10. resumen_cobertura_desbalanceada  secciones rezagadas vs promedio global

REGLA TRANSVERSAL: los códigos 97/98/99 son no-respuesta, nunca valores de
escala. Se excluyen de todo promedio y de toda batería. Sin esto, un "No sabe"
(98) se leería como la peor calificación posible, y una batería de puros 99
dispararía straightlining siendo en realidad una no-respuesta.
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd

from config import (
    DUR_MIN_MIN, DUR_MAX_MIN, DUR_FACTOR_AMARILLO,
    UMBRAL_DURACION_RACHA_MIN, MIN_RACHA_RAPIDA,
    HORA_INICIO_OPERATIVO, HORA_FIN_OPERATIVO,
    GRABACION_DURACION_MIN_SEG,
    BATERIAS_STRAIGHTLINING, CODIGOS_NO_SUSTANTIVOS,
    CANDIDATOS, ATRIBUTOS_BLOQUE7, ATRIBUTOS_COMPARABLES, ESCALAS,
    CANDIDATO_FOCO, SESGO_MIN_OTROS_CONOCIDOS,
    SESGO_ENC_MIN_ENCUESTAS, SESGO_ENC_DESVIO_AMARILLO, SESGO_ENC_DESVIO_ROJO,
    GEOJSON_SECCIONES,
)
import kpis

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _solo_sustantivos(serie: pd.Series) -> pd.Series:
    """Convierte 97/98/99 en NaN. Se aplica a toda columna 'codigo_*'."""
    s = pd.to_numeric(serie, errors="coerce")
    return s.where(~s.isin(CODIGOS_NO_SUSTANTIVOS))


def _marcar(d: pd.DataFrame, nombre: str,
            rojo: pd.Series, amarillo: pd.Series) -> pd.DataFrame:
    """Escribe el par de columnas flag_<nombre> / flag_<nombre>_nivel."""
    rojo = rojo.fillna(False).astype(bool)
    amarillo = (amarillo.fillna(False) & ~rojo).astype(bool)
    d[f"flag_{nombre}"] = rojo | amarillo
    d[f"flag_{nombre}_nivel"] = None
    d.loc[amarillo, f"flag_{nombre}_nivel"] = "amarillo"
    d.loc[rojo, f"flag_{nombre}_nivel"] = "rojo"
    return d


def _serie_bool(d: pd.DataFrame, col: str, defecto: bool = False) -> pd.Series:
    if col in d.columns:
        return d[col].fillna(defecto).astype(bool)
    return pd.Series(defecto, index=d.index)


# ══════════════════════════════════════════════════════════════════════════════
# 1. DURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def flag_duracion(df: pd.DataFrame) -> pd.DataFrame:
    """
    Solo se evalúa en encuestas terminadas: una en curso tiene fecha_modificacion
    todavía abierta y una duración artificialmente corta que inflaría falsos
    positivos.

    Rojo     duración < DUR_MIN_MIN o > DUR_MAX_MIN
    Amarillo entre DUR_MIN_MIN y DUR_MIN_MIN × DUR_FACTOR_AMARILLO
    """
    d = df.copy()
    dur = pd.to_numeric(d["duracion_min"], errors="coerce")
    evaluable = _serie_bool(d, "terminada")

    rojo = evaluable & ((dur < DUR_MIN_MIN) | (dur > DUR_MAX_MIN))
    amarillo = evaluable & dur.between(DUR_MIN_MIN, DUR_MIN_MIN * DUR_FACTOR_AMARILLO)
    return _marcar(d, "duracion", rojo, amarillo)


# ══════════════════════════════════════════════════════════════════════════════
# 2. STRAIGHTLINING
# ══════════════════════════════════════════════════════════════════════════════

def flag_straightlining(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rojo     una batería de bloque 7 (6 atributos de un aspirante) idéntica
    Amarillo una batería de bloque 6 (7 aspirantes de una pregunta) idéntica

    Vectorizado con nunique(axis=1) en vez del apply fila por fila de L2.
    Una batería incompleta no se evalúa: con 97/98/99 ya convertidos en NaN,
    se exige que TODOS los ítems tengan valor sustantivo antes de juzgar.
    """
    d = df.copy()
    terminada = _serie_bool(d, "terminada")

    detalle = pd.DataFrame(index=d.index)
    for nombre, columnas in BATERIAS_STRAIGHTLINING.items():
        existentes = [c for c in columnas if c in d.columns]
        if len(existentes) != len(columnas):
            continue   # batería no disponible en este df

        bloque = d[columnas].apply(_solo_sustantivos)
        completa = bloque.notna().sum(axis=1) == len(columnas)
        detalle[nombre] = completa & (bloque.nunique(axis=1, dropna=True) == 1)

    if detalle.empty:
        d["flag_straightlining_detalle"] = [[] for _ in range(len(d))]
        d["flag_straightlining_n_baterias"] = 0
        return _marcar(d, "straightlining",
                       pd.Series(False, index=d.index),
                       pd.Series(False, index=d.index))

    cols_b7 = [c for c in detalle.columns if c.startswith("bloque7_")]
    cols_b6 = [c for c in detalle.columns if c.startswith("bloque6_")]
    n_b7 = detalle[cols_b7].sum(axis=1) if cols_b7 else pd.Series(0, index=d.index)
    n_b6 = detalle[cols_b6].sum(axis=1) if cols_b6 else pd.Series(0, index=d.index)

    nombres = detalle.columns.to_numpy()
    d["flag_straightlining_detalle"] = [
        list(nombres[fila]) for fila in detalle.to_numpy(dtype=bool)
    ]
    d["flag_straightlining_n_baterias"] = n_b7 + n_b6

    return _marcar(d, "straightlining",
                   terminada & (n_b7 >= 1),
                   terminada & (n_b6 >= 1))


# ══════════════════════════════════════════════════════════════════════════════
# 3. GEORREFERENCIACIÓN
# ══════════════════════════════════════════════════════════════════════════════

_POLIGONOS: dict[int, object] | None = None


def _cargar_poligonos(path: str = GEOJSON_SECCIONES) -> dict[int, object]:
    global _POLIGONOS
    if _POLIGONOS is not None:
        return _POLIGONOS
    try:
        from shapely.geometry import shape
    except ImportError:
        logger.warning("shapely no disponible — flag_georef desactivado.")
        _POLIGONOS = {}
        return _POLIGONOS
    try:
        with open(path, encoding="utf-8") as f:
            gj = json.load(f)
    except OSError as e:
        logger.warning("No se pudo leer %s (%s) — flag_georef desactivado.", path, e)
        _POLIGONOS = {}
        return _POLIGONOS

    _POLIGONOS = {
        feat["properties"]["seccion"]: shape(feat["geometry"])
        for feat in gj["features"]
    }
    return _POLIGONOS


def _ubicar(lat, lon, poligonos: dict) -> int | None:
    if pd.isna(lat) or pd.isna(lon) or not poligonos:
        return None
    from shapely.geometry import Point
    punto = Point(lon, lat)   # shapely usa (x=lon, y=lat)
    for seccion, geom in poligonos.items():
        if geom.contains(punto):
            return seccion
    return None


def flag_georef_fuera_zona(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compara la sección DECLARADA por el encuestador contra la sección cuyo
    polígono contiene las coordenadas reportadas.

    Rojo     el punto no cae en ninguna sección de Atlixco
    Amarillo cae en otra sección de Atlixco
    (sin coordenadas → no evaluable)

    La evaluación es a nivel SECCIÓN, no de manzana: hay manzanas con pocas
    viviendas donde el encuestador legítimamente tiene que moverse. La
    distancia al punto asignado se conserva como diagnóstico (columna
    distancia_punto_m de la transformación), sin generar alerta.

    La sección 160 está reseccionada (vigentes 3119-3123) pero campo captura
    160 y el GeoJSON tiene su polígono viejo, que cubre el área completa.
    """
    d = df.copy()
    poligonos = _cargar_poligonos()

    if not poligonos:
        d["seccion_georef"] = pd.NA
        return _marcar(d, "georef",
                       pd.Series(False, index=d.index),
                       pd.Series(False, index=d.index))

    d["seccion_georef"] = [
        _ubicar(lat, lon, poligonos)
        for lat, lon in zip(d["latitud"], d["longitud"])
    ]

    con_coords = d["latitud"].notna() & d["longitud"].notna()
    fuera_municipio = con_coords & d["seccion_georef"].isna()
    otra_seccion = (
        con_coords
        & d["seccion_georef"].notna()
        & (d["seccion_georef"] != d["seccion_electoral"])
    )
    return _marcar(d, "georef", fuera_municipio, otra_seccion)


# ══════════════════════════════════════════════════════════════════════════════
# 4. HORARIO
# ══════════════════════════════════════════════════════════════════════════════

def flag_horario_atipico(df: pd.DataFrame) -> pd.DataFrame:
    """
    Amarillo: entrevista iniciada fuera del horario operativo declarado.
    fecha_creacion ya viene en hora local de México sin tzinfo — no se
    reconvierte aquí (ver nota de zona horaria en transform_atlixco_l3).
    """
    d = df.copy()
    hora = pd.to_datetime(d["fecha_creacion"], errors="coerce").dt.hour
    fuera = hora.notna() & ~hora.between(HORA_INICIO_OPERATIVO, HORA_FIN_OPERATIVO - 1)
    return _marcar(d, "horario", pd.Series(False, index=d.index), fuera)


# ══════════════════════════════════════════════════════════════════════════════
# 5. GRABACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def flag_grabacion(df: pd.DataFrame) -> pd.DataFrame:
    """
    La pregunta final (P19_1, preferencia MORENA) se graba en audio.

    Rojo     encuesta terminada sin archivo de grabación
    Amarillo grabación presente pero más corta que GRABACION_DURACION_MIN_SEG
             (audio vacío o cortado)

    Solo se evalúa en terminadas: una encuesta en curso todavía no llegó a la
    pregunta que se graba.
    """
    d = df.copy()
    terminada = _serie_bool(d, "terminada")
    tiene = _serie_bool(d, "tiene_grabacion")
    dur_seg = pd.to_numeric(d.get("grabacion_duracion_seg"), errors="coerce")

    rojo = terminada & ~tiene
    amarillo = terminada & tiene & (dur_seg < GRABACION_DURACION_MIN_SEG)
    return _marcar(d, "grabacion", rojo, amarillo)


# ══════════════════════════════════════════════════════════════════════════════
# 6. INCONSISTENCIA CONOCIMIENTO / ATRIBUTOS
# ══════════════════════════════════════════════════════════════════════════════

def flag_inconsistencia_conocimiento(df: pd.DataFrame) -> pd.DataFrame:
    """
    El instrumento filtra los atributos a quien respondió "Sí conoce" (código 1
    en P8). Por diseño, esta inconsistencia NO debería poder ocurrir: si
    aparece, es bug del formulario o captura fabricada. Debe disparar poco —
    si dispara mucho, revisar primero la lógica de salto en Bubble.

    Amarillo 2 aspirantes con el patrón
    Rojo     3 o más

    Cortes subidos respecto a L2 (1 → amarillo, 2+ → rojo) porque L3 pasa de
    5 a 7 aspirantes: con más aspirantes hay más oportunidades de inconsistencia
    por encuesta, y los cortes viejos marcarían más registros sin que la calidad
    haya cambiado.
    """
    d = df.copy()
    terminada = _serie_bool(d, "terminada")
    n_incons = pd.Series(0, index=d.index)
    detalle: list[list[str]] = [[] for _ in range(len(d))]

    for cand in CANDIDATOS:
        col_conoce = f"codigo_conocimiento_{cand}"
        if col_conoce not in d.columns:
            continue

        # 1 = Sí conoce · 2 = No conoce · 99 = No respondió
        no_conoce = _solo_sustantivos(d[col_conoce]) == 2

        cols_atrib = [
            f"codigo_{atrib}_{cand}" for atrib in ATRIBUTOS_BLOQUE7
            if f"codigo_{atrib}_{cand}" in d.columns
        ]
        if not cols_atrib:
            continue

        # "Opinó" = dio una respuesta sustantiva en al menos un atributo.
        # 98/99 ya son NaN, así que un "No sabe" no cuenta como opinión.
        opino = d[cols_atrib].apply(_solo_sustantivos).notna().any(axis=1)

        incons = terminada & no_conoce & opino
        n_incons += incons.astype(int)
        for i, marcado in enumerate(incons.to_numpy()):
            if marcado:
                detalle[i].append(cand)

    d["flag_inconsistencia_conocimiento_detalle"] = detalle
    d["flag_inconsistencia_conocimiento_n"] = n_incons
    return _marcar(d, "inconsistencia_conocimiento",
                   n_incons >= 3, n_incons == 2)


# ══════════════════════════════════════════════════════════════════════════════
# 7. SESGO HACIA EL ASPIRANTE FOCO — NIVEL REGISTRO
# ══════════════════════════════════════════════════════════════════════════════

def calcular_favorabilidad(df: pd.DataFrame, foco: str = CANDIDATO_FOCO) -> pd.DataFrame:
    """
    Agrega tres columnas, SIN alerta:

      eval_prom_<foco>      media del foco en los 4 atributos comparables
      eval_prom_otros       media de los demás aspirantes CONOCIDOS
      score_favorabilidad   eval_prom_otros − eval_prom_<foco>

    La escala va de 1 (más favorable) a 4 (menos), así que un score POSITIVO
    significa que el foco salió mejor evaluado que el resto. El rango práctico
    es −3 a +3.

    Base comparable: solo los 4 atributos con el mismo filtro ("sí conoce") y
    la misma escala 1-4. buena_candidatura queda fuera a propósito: el
    instrumento solo la pregunta a quien ya dio opinión "Buena", así que es una
    muestra autoseleccionada que inflaría el score artificialmente.
    """
    d = df.copy()

    def _media_atributos(cand: str) -> pd.Series:
        cols = [f"codigo_{a}_{cand}" for a in ATRIBUTOS_COMPARABLES
                if f"codigo_{a}_{cand}" in d.columns]
        if not cols:
            return pd.Series(np.nan, index=d.index)
        return d[cols].apply(_solo_sustantivos).mean(axis=1)

    medias = {cand: _media_atributos(cand) for cand in CANDIDATOS}
    otros = [c for c in CANDIDATOS if c != foco]

    d[f"eval_prom_{foco}"] = medias.get(foco, pd.Series(np.nan, index=d.index))
    d["eval_prom_otros"] = pd.concat([medias[c] for c in otros], axis=1).mean(axis=1)
    d["n_otros_evaluados"] = pd.concat(
        [medias[c].notna() for c in otros], axis=1
    ).sum(axis=1)

    d["score_favorabilidad"] = (d["eval_prom_otros"] - d[f"eval_prom_{foco}"]).round(2)
    return d


def flag_sesgo_foco(df: pd.DataFrame, foco: str = CANDIDATO_FOCO) -> pd.DataFrame:
    """
    Patrón extremo pro-foco a nivel registro.

    Un simpatizante genuino produce el mismo patrón que un formulario
    rellenado, así que este flag usa un corte casi imposible en la vida real y
    NO pretende identificar fraude por sí solo: sirve como insumo del flag
    por encuestador, que es donde está la señal.

    Rojo     el foco en el valor MÁS favorable de los 4 atributos comparables
             Y "votaría", mientras TODOS los demás aspirantes evaluados están
             en el valor MENOS favorable Y "nunca votaría"
    Amarillo lo mismo sin exigir el extremo en votar_o_no

    Requiere al menos SESGO_MIN_OTROS_CONOCIDOS aspirantes evaluados: con uno
    o dos, el patrón es ruido.
    """
    d = calcular_favorabilidad(df, foco)
    terminada = _serie_bool(d, "terminada")

    def _todos_en(cand: str, valor: int) -> pd.Series:
        cols = [f"codigo_{a}_{cand}" for a in ATRIBUTOS_COMPARABLES
                if f"codigo_{a}_{cand}" in d.columns]
        if not cols:
            return pd.Series(False, index=d.index)
        bloque = d[cols].apply(_solo_sustantivos)
        completa = bloque.notna().all(axis=1)
        return completa & (bloque == valor).all(axis=1)

    mejor = min(ESCALAS["honestidad"]["min"], ESCALAS["honestidad"]["max"])
    peor = max(ESCALAS["honestidad"]["min"], ESCALAS["honestidad"]["max"])

    foco_inmejorable = _todos_en(foco, mejor)
    otros = [c for c in CANDIDATOS if c != foco]

    # Todos los otros EVALUADOS en el peor valor. Un aspirante sin evaluar
    # (no lo conoce) no cuenta ni a favor ni en contra.
    otros_pesimos = pd.Series(True, index=d.index)
    for cand in otros:
        cols = [f"codigo_{a}_{cand}" for a in ATRIBUTOS_COMPARABLES
                if f"codigo_{a}_{cand}" in d.columns]
        if not cols:
            continue
        bloque = d[cols].apply(_solo_sustantivos)
        evaluado = bloque.notna().any(axis=1)
        otros_pesimos &= (~evaluado) | _todos_en(cand, peor)

    suficientes = d["n_otros_evaluados"] >= SESGO_MIN_OTROS_CONOCIDOS
    patron_atributos = terminada & suficientes & foco_inmejorable & otros_pesimos

    # Voto: foco "votaría" (1) y todos los demás "nunca votaría" (2)
    col_voto_foco = f"codigo_votar_o_no_{foco}"
    if col_voto_foco in d.columns:
        voto_foco = _solo_sustantivos(d[col_voto_foco]) == 1
        voto_otros = pd.Series(True, index=d.index)
        for cand in otros:
            col = f"codigo_votar_o_no_{cand}"
            if col in d.columns:
                v = _solo_sustantivos(d[col])
                voto_otros &= v.isna() | (v == 2)
        patron_voto = voto_foco & voto_otros
    else:
        patron_voto = pd.Series(False, index=d.index)

    return _marcar(d, "sesgo_foco",
                   patron_atributos & patron_voto,
                   patron_atributos)


# ══════════════════════════════════════════════════════════════════════════════
# 8. RACHA DE CAPTURAS RÁPIDAS — NIVEL ENCUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

def flag_racha_rapida(df: pd.DataFrame) -> pd.DataFrame:
    """
    MIN_RACHA_RAPIDA o más encuestas CONSECUTIVAS del mismo encuestador, el
    mismo día, por debajo de UMBRAL_DURACION_RACHA_MIN.

    Una encuesta rápida aislada es plausible — alguien eficiente, o un
    entrevistado que no conoce a ningún aspirante y se salta medio cuestionario
    por los filtros. Lo que amerita revisión es la racha seguida, no el tiempo
    absoluto de un registro suelto.

    Devuelve una fila por racha detectada (shape distinto al DataFrame base).
    """
    cols = ["encuestador_nombre", "dia", "n_encuestas", "duracion_prom",
            "hora_inicio", "hora_fin", "folios"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = df.copy()
    d["dia"] = pd.to_datetime(d["fecha_creacion"], errors="coerce").dt.date
    d = d[d["dia"].notna() & d["duracion_min"].notna()]

    rachas = []
    for (enc, dia), grupo in d.groupby(["encuestador_nombre", "dia"], dropna=False):
        grupo = grupo.sort_values("fecha_creacion")
        actual: list = []

        def _cerrar(bloque):
            if len(bloque) >= MIN_RACHA_RAPIDA:
                rachas.append({
                    "encuestador_nombre": enc,
                    "dia": dia,
                    "n_encuestas": len(bloque),
                    "duracion_prom": round(
                        sum(f["duracion_min"] for f in bloque) / len(bloque), 1),
                    "hora_inicio": bloque[0]["fecha_creacion"].strftime("%H:%M"),
                    "hora_fin": bloque[-1]["fecha_creacion"].strftime("%H:%M"),
                    "folios": ", ".join(str(f["folio"]) for f in bloque),
                })

        for _, fila in grupo.iterrows():
            if fila["duracion_min"] < UMBRAL_DURACION_RACHA_MIN:
                actual.append(fila)
            else:
                _cerrar(actual)
                actual = []
        _cerrar(actual)

    if not rachas:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rachas)[cols].sort_values(
        ["dia", "n_encuestas"], ascending=[False, False]
    ).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 9. SESGO PRO-FOCO — NIVEL ENCUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

def flag_sesgo_encuestador(df: pd.DataFrame,
                           foco: str = CANDIDATO_FOCO) -> pd.DataFrame:
    """
    Compara la tasa de registros pro-foco de cada encuestador contra la tasa
    GLOBAL del operativo, medida en desviaciones estándar.

    Esta es la pieza que de verdad sirve. A nivel registro, un simpatizante
    real y un formulario rellenado se ven igual. A nivel encuestador no: si
    Atlixco entero favorece al foco, eso sube la tasa global y nadie se marca
    por ello — lo que destaca es quien se aparta de sus propios compañeros
    trabajando el mismo municipio en las mismas fechas.

    "Pro-foco" = score_favorabilidad > 0, es decir, el foco mejor evaluado que
    el promedio de los demás aspirantes que el entrevistado conoce.

    Amarillo  desvío > SESGO_ENC_DESVIO_AMARILLO sigmas sobre la media global
    Rojo      desvío > SESGO_ENC_DESVIO_ROJO sigmas

    Encuestadores con menos de SESGO_ENC_MIN_ENCUESTAS registros evaluables no
    se juzgan: con pocos casos la tasa es ruido.
    """
    cols = ["encuestador_nombre", "encuestador_id", "n_evaluables", "n_pro_foco",
            "tasa_pro_foco", "tasa_global", "desvio_sigmas",
            "score_prom", "flag_sesgo_encuestador_nivel"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = df if "score_favorabilidad" in df.columns else calcular_favorabilidad(df, foco)
    d = d[d["score_favorabilidad"].notna()].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)

    d["_pro_foco"] = d["score_favorabilidad"] > 0

    g = d.groupby(["encuestador_nombre", "encuestador_id"], dropna=False).agg(
        n_evaluables=("_pro_foco", "size"),
        n_pro_foco=("_pro_foco", "sum"),
        score_prom=("score_favorabilidad", "mean"),
    ).reset_index()

    g["tasa_pro_foco"] = (g["n_pro_foco"] / g["n_evaluables"]).round(3)
    g["score_prom"] = g["score_prom"].round(2)

    tasa_global = float(d["_pro_foco"].mean())
    g["tasa_global"] = round(tasa_global, 3)

    # Error estándar binomial por encuestador: con n pequeño la tasa oscila
    # más, y dividir por él evita castigar a quien tiene pocos registros.
    evaluables = g["n_evaluables"].clip(lower=1)
    error_est = np.sqrt(max(tasa_global * (1 - tasa_global), 1e-9) / evaluables)
    g["desvio_sigmas"] = ((g["tasa_pro_foco"] - tasa_global) / error_est).round(2)

    suficientes = g["n_evaluables"] >= SESGO_ENC_MIN_ENCUESTAS
    rojo = suficientes & (g["desvio_sigmas"] > SESGO_ENC_DESVIO_ROJO)
    amarillo = suficientes & ~rojo & (g["desvio_sigmas"] > SESGO_ENC_DESVIO_AMARILLO)

    g["flag_sesgo_encuestador_nivel"] = None
    g.loc[amarillo, "flag_sesgo_encuestador_nivel"] = "amarillo"
    g.loc[rojo, "flag_sesgo_encuestador_nivel"] = "rojo"

    return g[cols].sort_values("desvio_sigmas", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 10. COBERTURA GEOGRÁFICA DESBALANCEADA — NIVEL SECCIÓN
# ══════════════════════════════════════════════════════════════════════════════

def resumen_cobertura_desbalanceada(df: pd.DataFrame) -> pd.DataFrame:
    """
    Secciones cuyo avance se desvía del promedio GLOBAL del operativo.
    En L2 la comparación era contra el promedio de la zona; sin zonas en L3,
    la referencia es el avance global.

    Rojo     avance 0 habiendo ya avance en otras secciones
    Amarillo avance < 50% del promedio global
    """
    av = kpis.avance_por_seccion(df)
    if av.empty:
        return av

    prom_global = av["pct"].mean()
    hay_avance = av["avance"].max() > 0

    rojo = (av["avance"] == 0) & hay_avance
    amarillo = (~rojo) & (av["pct"] < 0.5 * prom_global) & (prom_global > 0)

    av["flag_cobertura"] = rojo | amarillo
    av["flag_cobertura_nivel"] = None
    av.loc[amarillo, "flag_cobertura_nivel"] = "amarillo"
    av.loc[rojo, "flag_cobertura_nivel"] = "rojo"
    return av


# ══════════════════════════════════════════════════════════════════════════════
# ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

def aplicar_todos_los_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encadena los flags de nivel registro y agrega los conteos por semáforo.

    Los de nivel agregado se llaman por separado, devuelven otro shape:
        flag_racha_rapida(df)
        flag_sesgo_encuestador(df)
        resumen_cobertura_desbalanceada(df)
    """
    if df.empty:
        return df

    d = flag_duracion(df)
    d = flag_straightlining(d)
    d = flag_georef_fuera_zona(d)
    d = flag_horario_atipico(d)
    d = flag_grabacion(d)
    d = flag_inconsistencia_conocimiento(d)
    d = flag_sesgo_foco(d)

    cols_nivel = [c for c in d.columns
                  if c.startswith("flag_") and c.endswith("_nivel")]
    d["flags_rojo_n"] = (d[cols_nivel] == "rojo").sum(axis=1)
    d["flags_amarillo_n"] = (d[cols_nivel] == "amarillo").sum(axis=1)
    return d
