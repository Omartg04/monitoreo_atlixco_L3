"""
Atlixco PIE L3 · kpis.py
Avance vs meta: global, por sección, por encuestador, por estrato, y cobertura
de cuotas demográficas y de manzanas.

Diferencias vs L2:
  - Sin capa de ZONA. Las 45 secciones se monitorean planas; `estrato`
    (Urbana/Rural/Mixta) sustituye a la zona como única agregación intermedia,
    y es descriptivo, no operativo.
  - Cuotas con grid sexo x 4 rangos de edad (L2 era solo H/M).
  - Nuevo: cobertura de manzanas asignadas — de las 200 manzanas de la muestra,
    cuáles no tienen todavía ninguna encuesta.

Columnas mínimas en `df` (salida de transform_atlixco_l3):
    folio, seccion_electoral, estrato, encuestador_id, encuestador_nombre,
    terminada, duracion_min, sexo_cuota, rango_edad_cuota, punto_asignado
"""
from __future__ import annotations

import pandas as pd

from config import (
    GRABACION_DURACION_MIN_SEG,
    META_GLOBAL, METAS_POR_SECCION, META_POR_ESTRATO, SECCIONES_POR_ESTRATO,
    PUNTOS_MUESTRA, PUNTOS_POR_SECCION,
    ETIQUETAS_RANGO_EDAD, SEXOS_CUOTA, CUOTAS_GLOBALES, cuotas_de_seccion,
)


# ══════════════════════════════════════════════════════════════════════════════
# SEMÁFORO
# ══════════════════════════════════════════════════════════════════════════════

def _semaforo(pct: float) -> str:
    if pct >= 100:
        return "verde"
    if pct >= 60:
        return "amarillo"
    return "rojo"


# ══════════════════════════════════════════════════════════════════════════════
# KPI 1 · META GLOBAL VS AVANCE
# ══════════════════════════════════════════════════════════════════════════════

def resumen_global(df: pd.DataFrame) -> dict:
    """
    El avance se mide con encuestas TERMINADAS, no levantadas: una encuesta
    abandonada a la mitad no cubre una unidad de la muestra.
    """
    terminadas = int(df["terminada"].sum()) if not df.empty else 0
    levantadas = len(df)

    return {
        "meta_global": META_GLOBAL,
        "levantadas": levantadas,
        "terminadas": terminadas,
        "incompletas": levantadas - terminadas,
        "avance_pct": round(100 * terminadas / META_GLOBAL, 1) if META_GLOBAL else 0.0,
        "faltan": max(META_GLOBAL - terminadas, 0),
        "secciones_en_meta": int(avance_por_seccion(df)["cubierta"].sum()),
        "secciones_totales": len(METAS_POR_SECCION),
    }


# ══════════════════════════════════════════════════════════════════════════════
# KPI 2 · AVANCE POR SECCIÓN
# ══════════════════════════════════════════════════════════════════════════════

def avance_por_seccion(df: pd.DataFrame) -> pd.DataFrame:
    """
    Una fila por sección, incluyendo las que aún tienen avance 0 — si solo
    aparecieran las trabajadas, las secciones sin desplegar serían invisibles,
    que es justo lo que hay que ver.
    """
    if df.empty:
        terminadas = pd.Series(dtype=int)
        manzanas_cub = pd.Series(dtype=int)
    else:
        d_term = df[df["terminada"]]
        terminadas = d_term.groupby("seccion_electoral").size()
        manzanas_cub = (
            d_term[d_term["punto_asignado"].notna()]
            .groupby("seccion_electoral")["punto_asignado"].nunique()
        )

    filas = []
    for seccion, info in METAS_POR_SECCION.items():
        avance = int(terminadas.get(seccion, 0))
        meta = info["meta_encuestas"]
        pct = round(100 * avance / meta, 1) if meta else 0.0
        n_manzanas = info["manzanas"]
        cubiertas = int(manzanas_cub.get(seccion, 0))
        filas.append({
            "seccion": seccion,
            "estrato": info["estrato"],
            "manzanas": n_manzanas,
            "manzanas_cubiertas": cubiertas,
            "manzanas_sin_cobertura": max(n_manzanas - cubiertas, 0),
            "meta": meta,
            "avance": avance,
            "pct": pct,
            "faltan": max(meta - avance, 0),
            "cubierta": avance >= meta,
            "semaforo": _semaforo(pct),
            "observaciones": info.get("observaciones"),
        })

    return pd.DataFrame(filas).sort_values("seccion").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 3 · AVANCE POR ESTRATO
# ══════════════════════════════════════════════════════════════════════════════

def avance_por_estrato(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agregación descriptiva (Urbana / Rural / Mixta). No corresponde a brigadas:
    en L3 no hay asignación de equipos por geografía. Sirve para detectar si el
    rezago se concentra en lo rural, que es donde el desplazamiento cuesta más.
    """
    av = avance_por_seccion(df).set_index("seccion")

    filas = []
    for estrato, secciones in SECCIONES_POR_ESTRATO.items():
        meta = META_POR_ESTRATO[estrato]
        avance = int(av.loc[secciones, "avance"].sum())
        pct = round(100 * avance / meta, 1) if meta else 0.0
        filas.append({
            "estrato": estrato,
            "n_secciones": len(secciones),
            "meta": meta,
            "avance": avance,
            "pct": pct,
            "faltan": max(meta - avance, 0),
            "semaforo": _semaforo(pct),
        })

    return pd.DataFrame(filas).sort_values("pct").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 4 · AVANCE POR ENCUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

def avance_por_encuestador(df: pd.DataFrame,
                           seccion: int | None = None) -> pd.DataFrame:
    """
    Levantadas, terminadas, tasa de completitud, duración promedio y cobertura
    territorial por encuestador. La tasa de completitud separa a quien levanta
    mucho y termina poco — patrón distinto al de quien simplemente va lento.
    """
    cols = ["encuestador_id", "encuestador_nombre", "levantadas", "terminadas",
            "pct_completitud", "duracion_prom_min", "duracion_mediana_min",
            "secciones_trabajadas", "manzanas_trabajadas", "dias_activos"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = df if seccion is None else df[df["seccion_electoral"] == seccion]
    if d.empty:
        return pd.DataFrame(columns=cols)

    d = d.copy()
    d["_dia"] = pd.to_datetime(d["fecha_creacion"], errors="coerce").dt.date

    g = d.groupby(["encuestador_id", "encuestador_nombre"], dropna=False)
    out = g.agg(
        levantadas=("folio", "count"),
        terminadas=("terminada", "sum"),
        duracion_prom_min=("duracion_min", "mean"),
        duracion_mediana_min=("duracion_min", "median"),
        secciones_trabajadas=("seccion_electoral", "nunique"),
        manzanas_trabajadas=("punto_asignado", "nunique"),
        dias_activos=("_dia", "nunique"),
    ).reset_index()

    out["terminadas"] = out["terminadas"].astype(int)
    out["pct_completitud"] = (
        100 * out["terminadas"] / out["levantadas"].clip(lower=1)
    ).round(1)
    out["duracion_prom_min"] = out["duracion_prom_min"].round(1)
    out["duracion_mediana_min"] = out["duracion_mediana_min"].round(1)

    return out[cols].sort_values("terminadas", ascending=False).reset_index(drop=True)


def avance_diario(df: pd.DataFrame) -> pd.DataFrame:
    """Terminadas por día y acumulado — para ver el ritmo contra la meta."""
    if df.empty:
        return pd.DataFrame(columns=["dia", "levantadas", "terminadas",
                                     "acumulado", "pct_meta_acumulado"])
    d = df.copy()
    d["dia"] = pd.to_datetime(d["fecha_creacion"], errors="coerce").dt.date

    out = d.groupby("dia").agg(
        levantadas=("folio", "count"),
        terminadas=("terminada", "sum"),
    ).reset_index().sort_values("dia")

    out["terminadas"] = out["terminadas"].astype(int)
    out["acumulado"] = out["terminadas"].cumsum()
    out["pct_meta_acumulado"] = (100 * out["acumulado"] / META_GLOBAL).round(1)
    return out.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 5 · COBERTURA DE CUOTAS (sexo x rango de edad)
# ══════════════════════════════════════════════════════════════════════════════

def _conteo_cuotas(d: pd.DataFrame) -> pd.Series:
    """Conteo por celda (rango_edad, sexo) entre encuestas terminadas."""
    if d.empty:
        return pd.Series(dtype=int)
    t = d[d["terminada"]]
    if t.empty:
        return pd.Series(dtype=int)
    return t.groupby(["rango_edad_cuota", "sexo_cuota"]).size()


def avance_cuotas(df: pd.DataFrame, seccion: int) -> pd.DataFrame:
    """
    Meta vs avance por celda sexo x rango de edad para UNA sección.
    8 filas: 4 rangos x H/M.
    """
    cuotas = cuotas_de_seccion(seccion)
    conteo = _conteo_cuotas(df[df["seccion_electoral"] == seccion])

    filas = []
    for rango in ETIQUETAS_RANGO_EDAD:
        for sexo in SEXOS_CUOTA:
            meta = cuotas[rango][sexo]
            avance = int(conteo.get((rango, sexo), 0))
            pct = round(100 * avance / meta, 1) if meta else 0.0
            filas.append({
                "seccion": seccion, "rango_edad": rango, "sexo": sexo,
                "meta": meta, "avance": avance, "pct": pct,
                "faltan": max(meta - avance, 0),
                "semaforo": _semaforo(pct),
            })
    return pd.DataFrame(filas)


def avance_cuotas_global(df: pd.DataFrame) -> pd.DataFrame:
    """
    Meta vs avance por celda sobre el operativo completo.
    La meta sale de CUOTAS_GLOBALES (repartida sobre META_GLOBAL), no de sumar
    las cuotas por sección: el redondeo sección por sección introduce
    desviaciones de +-1 celda que al agregarse distorsionan la distribución.
    """
    conteo = _conteo_cuotas(df)

    filas = []
    for rango in ETIQUETAS_RANGO_EDAD:
        for sexo in SEXOS_CUOTA:
            meta = CUOTAS_GLOBALES[(rango, sexo)]
            avance = int(conteo.get((rango, sexo), 0))
            pct = round(100 * avance / meta, 1) if meta else 0.0
            filas.append({
                "rango_edad": rango, "sexo": sexo,
                "meta": meta, "avance": avance, "pct": pct,
                "faltan": max(meta - avance, 0),
                "semaforo": _semaforo(pct),
            })
    return pd.DataFrame(filas)


def composicion_lograda(df: pd.DataFrame,
                        seccion: int | None = None) -> pd.DataFrame:
    """
    Composición demográfica REAL de lo levantado, sin referencia a metas.

    Se usa en lugar de avance_cuotas mientras DISTRIBUCION_CUOTAS siga siendo
    placeholder: comparar avance contra una meta inventada produce semáforos
    que mienten, y un coordinador que ve rojo en una celda sale a perseguir una
    cuota que nadie calibró. La composición lograda, en cambio, es un hecho
    observado: sirve para detectar desequilibrios (todo hombres jóvenes, nadie
    de 60+) sin afirmar cuál debería ser la proporción correcta.

    Cuando las cuotas se calibren contra el marco muestral de Atlixco, se puede
    volver a avance_cuotas / sesgo_cuotas, que ya están implementadas.
    """
    cols = ["rango_edad", "sexo", "n", "pct_logrado"]
    d = df if seccion is None else df[df["seccion_electoral"] == seccion]
    conteo = _conteo_cuotas(d)
    total = int(conteo.sum()) if len(conteo) else 0

    filas = []
    for rango in ETIQUETAS_RANGO_EDAD:
        for sexo in SEXOS_CUOTA:
            n = int(conteo.get((rango, sexo), 0))
            filas.append({
                "rango_edad": rango, "sexo": sexo, "n": n,
                "pct_logrado": round(100 * n / total, 1) if total else 0.0,
            })
    out = pd.DataFrame(filas)[cols]

    # Registros terminados sin sexo o sin edad válida: no entran a ninguna
    # celda, así que hay que reportarlos aparte o el total no cuadra.
    if not d.empty:
        t = d[d["terminada"]]
        sin_clasificar = int(
            (t["sexo_cuota"].isna() | t["rango_edad_cuota"].isna()).sum()
        )
    else:
        sin_clasificar = 0
    out.attrs["total_clasificados"] = total
    out.attrs["sin_clasificar"] = sin_clasificar
    return out


def sesgo_cuotas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Desviación de la composición LOGRADA respecto a la objetivo, en puntos
    porcentuales. Distinto del avance: una cuota puede ir al 50% en todas las
    celdas (avance parejo, sin sesgo) o al 90% en jóvenes y 10% en adultos
    mayores (mismo avance global, sesgo fuerte). Esto último es lo que hay que
    corregir en campo mientras aún se puede.
    """
    cuotas = avance_cuotas_global(df)
    total_avance = cuotas["avance"].sum()
    total_meta = cuotas["meta"].sum()

    cuotas["pct_logrado"] = (
        100 * cuotas["avance"] / max(total_avance, 1)
    ).round(1)
    cuotas["pct_objetivo"] = (
        100 * cuotas["meta"] / max(total_meta, 1)
    ).round(1)
    cuotas["desviacion_pp"] = (cuotas["pct_logrado"] - cuotas["pct_objetivo"]).round(1)

    return cuotas[["rango_edad", "sexo", "pct_objetivo", "pct_logrado",
                   "desviacion_pp", "meta", "avance", "faltan"]]


# ══════════════════════════════════════════════════════════════════════════════
# KPI 6 · COBERTURA DE MANZANAS ASIGNADAS
# ══════════════════════════════════════════════════════════════════════════════

def cobertura_manzanas(df: pd.DataFrame,
                       seccion: int | None = None) -> pd.DataFrame:
    """
    Una fila por manzana de la muestra, con cuántas encuestas terminadas tiene
    contra su objetivo (4 por manzana).

    Es el KPI más accionable para coordinación de campo: "en estas 5 manzanas
    de la 160 no hay ni una encuesta" dice dónde mandar gente. Más útil que
    cualquier alerta por registro.

    La asignación de encuesta a manzana se hace por cercanía dentro de la
    sección declarada (ver transform_atlixco_l3._punto_mas_cercano), así que es
    aproximada: una encuesta levantada entre dos manzanas se atribuye a la más
    próxima. Sirve para ver huecos, no para auditar manzana por manzana.
    """
    puntos = PUNTOS_MUESTRA if seccion is None else PUNTOS_POR_SECCION.get(seccion, [])
    if not puntos:
        return pd.DataFrame(columns=["punto_id", "seccion", "estrato", "localidad",
                                     "manzana", "meta_punto", "avance", "pct",
                                     "latitud", "longitud", "semaforo"])

    if df.empty:
        conteo = pd.Series(dtype=int)
    else:
        t = df[df["terminada"] & df["punto_asignado"].notna()]
        conteo = t.groupby("punto_asignado").size()

    filas = []
    for p in puntos:
        avance = int(conteo.get(p["punto_id"], 0))
        meta = p["meta_punto"]
        pct = round(100 * avance / meta, 1) if meta else 0.0
        filas.append({
            "punto_id": p["punto_id"],
            "seccion": p["seccion"],
            "estrato": p["estrato"],
            "localidad": p["localidad"],
            "manzana": p["manzana"],
            "meta_punto": meta,
            "avance": avance,
            "pct": pct,
            "latitud": p["latitud"],
            "longitud": p["longitud"],
            "semaforo": _semaforo(pct),
        })

    return pd.DataFrame(filas).sort_values(
        ["seccion", "pct"], ascending=[True, True]
    ).reset_index(drop=True)


def manzanas_sin_cobertura(df: pd.DataFrame) -> pd.DataFrame:
    """Solo las manzanas de la muestra sin ninguna encuesta terminada."""
    cob = cobertura_manzanas(df)
    return cob[cob["avance"] == 0].reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 7 · GRABACIONES DE AUDIO (P19_1)
# ══════════════════════════════════════════════════════════════════════════════

def resumen_grabaciones(df: pd.DataFrame) -> dict:
    """
    Cobertura y calidad de las grabaciones de la pregunta P19_1.

    Desde la v2 del cuestionario P19_1 está en la pantalla 8 de 16, no al
    final. Eso cambia la lectura: un registro puede tener audio y NO estar
    terminado, algo imposible en la v1. Esa combinación mide abandono entre
    las pantallas 8 y 15, que es información nueva.
    """
    if df.empty:
        return {"terminadas": 0, "con_audio": 0, "pct_cobertura": 0.0,
                "sin_audio": 0, "audio_corto": 0, "minutos_totales": 0.0,
                "duracion_mediana_seg": 0.0, "audio_sin_terminar": 0}

    terminadas = df[df["terminada"]]
    tiene = df["tiene_grabacion"].fillna(False)
    dur = pd.to_numeric(df["grabacion_duracion_seg"], errors="coerce")

    con_audio = int((terminadas["tiene_grabacion"].fillna(False)).sum())
    n_term = len(terminadas)
    dur_term = pd.to_numeric(terminadas["grabacion_duracion_seg"], errors="coerce")

    return {
        "terminadas": n_term,
        "con_audio": con_audio,
        "pct_cobertura": round(100 * con_audio / n_term, 1) if n_term else 0.0,
        "sin_audio": n_term - con_audio,
        "audio_corto": int((dur_term < GRABACION_DURACION_MIN_SEG).sum()),
        "minutos_totales": round(float(dur[tiene].sum()) / 60, 1),
        "duracion_mediana_seg": round(float(dur_term.median()), 1)
                                if dur_term.notna().any() else 0.0,
        # Audio presente en encuestas NO terminadas: solo posible desde la v2,
        # donde P19_1 va antes que el bloque de atributos.
        "audio_sin_terminar": int((tiene & ~df["terminada"].fillna(False)).sum()),
    }


def grabaciones_por_encuestador(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tasa de grabación y duración por encuestador.

    Una tasa baja concentrada en una persona suele ser un permiso de micrófono
    denegado en su dispositivo, no desempeño: conviene revisarlo con ella antes
    de tratarlo como incumplimiento.
    """
    cols = ["encuestador_nombre", "terminadas", "con_audio", "pct_audio",
            "duracion_mediana_seg", "audio_corto"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = df[df["terminada"]].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)

    d["_dur"] = pd.to_numeric(d["grabacion_duracion_seg"], errors="coerce")
    d["_tiene"] = d["tiene_grabacion"].fillna(False)

    g = d.groupby("encuestador_nombre", dropna=False).agg(
        terminadas=("folio", "count"),
        con_audio=("_tiene", "sum"),
        duracion_mediana_seg=("_dur", "median"),
        audio_corto=("_dur", lambda s: int((s < GRABACION_DURACION_MIN_SEG).sum())),
    ).reset_index()

    g["con_audio"] = g["con_audio"].astype(int)
    g["pct_audio"] = (100 * g["con_audio"] / g["terminadas"].clip(lower=1)).round(1)
    g["duracion_mediana_seg"] = g["duracion_mediana_seg"].round(1)
    return g[cols].sort_values("pct_audio").reset_index(drop=True)


def grabaciones_pendientes(df: pd.DataFrame) -> pd.DataFrame:
    """Encuestas terminadas sin audio o con audio demasiado corto."""
    cols = ["folio", "fecha", "encuestador_nombre", "seccion_electoral",
            "grabacion_duracion_seg", "motivo", "grabacion_url"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = df[df["terminada"]].copy()
    d["_dur"] = pd.to_numeric(d["grabacion_duracion_seg"], errors="coerce")
    d["_tiene"] = d["tiene_grabacion"].fillna(False)

    sin = ~d["_tiene"]
    corto = d["_tiene"] & (d["_dur"] < GRABACION_DURACION_MIN_SEG)
    d = d[sin | corto].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)

    d["motivo"] = "sin grabación"
    d.loc[corto, "motivo"] = (
        d.loc[corto, "_dur"].apply(lambda s: f"audio de {s:.0f} s"))
    return d[cols].sort_values(["fecha", "encuestador_nombre"]).reset_index(drop=True)