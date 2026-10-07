"""
Atlixco PIE L3 — Visualizador de Monitoreo y Auditoría de Encuestas
Atlixco, Puebla · Encuesta L3 · 2026

Ejecutar:  streamlit run app.py

Cinco tabs:
  0. Planeación           muestra, metas y manzanas — estático, sin Bubble
  1. Avance vs Meta       global, por sección, por encuestador, cuotas
  2. Mapa de Cobertura    OpenStreetMap + vista satelital
  3. Flags                calidad y sesgo
  4. Resultados           tabulados del instrumento (solo rol estatal)

Sin agrupación por zonas: las 45 secciones se monitorean planas.
"""
import copy
import json
import time
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit_authenticator as stauth
from streamlit_authenticator.utilities import LoginError
from streamlit_folium import st_folium

from config import (
    VERDE, VERDE_L, AZUL, AZUL_L, NARANJA, ROJO, AMARILLO, GRIS_BG,
    ESTRATO_COLORES, DUR_MIN_MIN, DUR_MAX_MIN, AUTO_REFRESH_SEC,
    META_GLOBAL, METAS_POR_SECCION, META_POR_ESTRATO, SECCIONES_POR_ESTRATO,
    ESTRATOS, SECCIONES_FUERA_MUESTRA, SECCIONES_RESECCIONADAS,
    GEOJSON_SECCIONES, ATLIXCO_CENTRO, ATLIXCO_ZOOM,
    PUNTOS_MUESTRA, PUNTOS_POR_SECCION, DIST_MANZANA_REFERENCIA_M,
    CANDIDATOS, ATRIBUTOS_BLOQUE7, ETIQUETAS_ATRIBUTO, CANDIDATO_FOCO,
    CUOTAS_GLOBALES, ETIQUETAS_RANGO_EDAD, SEXOS_CUOTA,
    ROLES, MODO, INICIO_OPERATIVO, CRITERIO_TERMINADA, META_ENCUESTAS_DIARIAS,
    MAPAS_CAMPO,
)
import flags
import kpis
from transform_atlixco_l3 import get_encuestas

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE PÁGINA
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Atlixco PIE L3 · 2026",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap');
html, body, [class*="css"] {{ font-family: 'IBM Plex Sans', sans-serif; background: {GRIS_BG}; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; }}

.atx-header {{
    background: linear-gradient(120deg, {AZUL} 0%, {AZUL_L} 60%, {VERDE} 100%);
    color: white; padding: 16px 26px; border-radius: 10px;
    display: flex; align-items: center; gap: 18px; margin-bottom: 1rem;
}}
.atx-header h1 {{ margin: 0; font-size: 1.45rem; font-weight: 700; line-height: 1.2; }}
.atx-header p  {{ margin: 3px 0 0; font-size: 0.82rem; opacity: 0.85; }}

.kpi-card {{
    background: white; border-radius: 9px; padding: 14px 18px;
    border-left: 5px solid {VERDE}; box-shadow: 0 2px 6px rgba(0,0,0,.07); height: 100%;
}}
.kpi-val   {{ font-size: 1.9rem; font-weight: 700; color: {VERDE};
              font-family: 'IBM Plex Mono', monospace; line-height: 1.05; }}
.kpi-label {{ font-size: 0.72rem; color: #555; text-transform: uppercase;
              letter-spacing:.05em; margin-top:5px; }}
.kpi-sub   {{ font-size: 0.76rem; color: #888; margin-top: 3px; }}
.kpi-card.azul     {{ border-left-color: {AZUL_L}; }}
.kpi-card.azul .kpi-val     {{ color: {AZUL_L}; }}
.kpi-card.naranja  {{ border-left-color: {NARANJA}; }}
.kpi-card.naranja .kpi-val  {{ color: {NARANJA}; }}
.kpi-card.rojo     {{ border-left-color: {ROJO}; }}
.kpi-card.rojo .kpi-val     {{ color: {ROJO}; }}
.kpi-card.amarillo {{ border-left-color: {AMARILLO}; }}
.kpi-card.amarillo .kpi-val {{ color: {AMARILLO}; }}

.sec-title {{
    font-size: 1rem; font-weight: 600; color: {AZUL};
    border-bottom: 2px solid {VERDE_L}; padding-bottom: 3px; margin: 18px 0 10px;
}}
.ts-badge {{
    font-size: 0.72rem; color: #888; font-family: 'IBM Plex Mono', monospace;
    background: #eef1f5; border-radius: 4px; padding: 2px 8px; display: inline-block;
}}
.banner-test {{
    background: {ROJO}; color: white; padding: 8px 16px; border-radius: 6px;
    font-weight: 600; margin-bottom: 12px; font-size: 0.88rem;
}}
section[data-testid="stSidebar"] {{ background: {AZUL}; }}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] .stMarkdown p {{ color: #B8CDE0 !important; }}
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: white !important; }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════════════════════
_usuarios = st.secrets.get("auth", {}).get("credentials", {}).get("usernames", {})
_credenciales = {
    "usernames": {
        str(u): {"name": str(d["name"]), "password": str(d["password"])}
        for u, d in _usuarios.items()
    }
}
_cookie_nombre = str(st.secrets.get("auth", {}).get("cookie_name", "atlixco_l3_session"))
_cookie_clave = str(st.secrets.get("auth", {}).get("cookie_key", "atlixco_l3_dev_key_cambiar"))
_cookie_dias = int(st.secrets.get("auth", {}).get("cookie_expiry_days", 1))

autenticador = stauth.Authenticate(_credenciales, _cookie_nombre, _cookie_clave, _cookie_dias)

try:
    autenticador.login(location="main", key="atlixco_l3_login")
except LoginError as e:
    st.error(str(e))
    st.stop()

if not st.session_state.get("authentication_status"):
    if st.session_state.get("authentication_status") is False:
        st.error("Usuario o contraseña incorrectos.")
    else:
        st.info("Ingresa tus credenciales para acceder a Atlixco PIE L3.")
    st.stop()

_usuario = st.session_state["username"]
_cfg_rol = ROLES.get(_usuario, {"rol": "parcial", "secciones": []})
_rol = _cfg_rol.get("rol", "parcial")
_secciones_permitidas = _cfg_rol.get("secciones", [])

with st.sidebar:
    autenticador.logout(button_name="🔒 Cerrar sesión", location="sidebar",
                        key="atlixco_l3_logout")
    st.markdown(f"**{st.session_state.get('name', _usuario)}**")
    st.caption("Coordinación estatal" if _rol == "estatal"
               else f"Acceso parcial · {len(_secciones_permitidas)} secciones")
    st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH
# ══════════════════════════════════════════════════════════════════════════════
if "ultimo_refresh" not in st.session_state:
    st.session_state["ultimo_refresh"] = time.time()

if time.time() - st.session_state["ultimo_refresh"] > AUTO_REFRESH_SEC:
    st.session_state["ultimo_refresh"] = time.time()
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# CARGA DE DATOS
# ══════════════════════════════════════════════════════════════════════════════
PRIVATE_KEY = st.secrets.get("bubble", {}).get("private_key", "")

_filtro_secciones = None if _rol == "estatal" else _secciones_permitidas

with st.spinner("Cargando datos del operativo…"):
    df_raw, ultima_actualizacion, info_carga = get_encuestas(
        PRIVATE_KEY, secciones=_filtro_secciones
    )

if not df_raw.empty:
    with st.spinner("Calculando flags de calidad…"):
        df_raw = flags.aplicar_todos_los_flags(df_raw)


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_geojson(ruta_str: str):
    ruta = Path(ruta_str)
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


GEOJSON = cargar_geojson(GEOJSON_SECCIONES)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE PRESENTACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def kpi(col, valor, etiqueta, sub="", clase=""):
    col.markdown(f"""
    <div class="kpi-card {clase}">
      <div class="kpi-val">{valor}</div>
      <div class="kpi-label">{etiqueta}</div>
      <div class="kpi-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)


def color_semaforo(v):
    return {
        "verde":    "background-color:#D4EDDA;color:#155724;font-weight:700",
        "amarillo": "background-color:#FFF3CD;color:#856404;font-weight:700",
        "rojo":     "background-color:#F8D7DA;color:#721C24;font-weight:700",
    }.get(v, "")


def color_pct(v):
    if pd.isna(v):
        return ""
    if v >= 100:
        return "background-color:#D4EDDA;color:#155724;font-weight:700"
    if v >= 60:
        return "background-color:#FFF3CD;color:#856404;font-weight:700"
    return "background-color:#F8D7DA;color:#721C24;font-weight:700"


def color_duracion(v):
    if pd.isna(v):
        return ""
    if DUR_MIN_MIN <= v <= DUR_MAX_MIN:
        return "background-color:#D4EDDA;color:#155724;font-weight:600"
    if v < DUR_MIN_MIN:
        return "background-color:#F8D7DA;color:#721C24;font-weight:600"
    return "background-color:#FFF3CD;color:#856404;font-weight:600"


def color_desviacion(v):
    """Para sesgo de cuotas: desviación en puntos porcentuales, signo indiferente."""
    if pd.isna(v):
        return ""
    a = abs(v)
    if a >= 10:
        return "background-color:#F8D7DA;color:#721C24;font-weight:700"
    if a >= 5:
        return "background-color:#FFF3CD;color:#856404;font-weight:700"
    return "background-color:#D4EDDA;color:#155724;font-weight:600"


def tabla(df_in, estilos=None, formatos=None, altura=None, **kw):
    """
    st.dataframe con el estilo tipográfico del tablero.

    `altura` se omite del todo cuando es None, en vez de pasarse como
    height=None: a partir de Streamlit 1.69 ese valor se valida y lanza
    StreamlitInvalidHeightError. Omitir el argumento deja que Streamlit calcule
    la altura, que es el comportamiento deseado, y funciona en toda versión.
    """
    s = df_in.style
    for fn, columnas in (estilos or []):
        s = s.map(fn, subset=columnas)
    if formatos:
        s = s.format(formatos)
    s = s.set_properties(**{"font-family": "IBM Plex Sans", "font-size": "13px"})

    if altura is not None:
        kw["height"] = int(altura)
    st.dataframe(s, use_container_width=True, hide_index=True, **kw)


def bounds_de_geometria(geometria: dict):
    """Bounds [[lat_min,lon_min],[lat_max,lon_max]] para fit_bounds, sin shapely."""
    lons, lats = [], []

    def _recorrer(coords):
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0])
            lats.append(coords[1])
        else:
            for c in coords:
                _recorrer(c)

    _recorrer(geometria["coordinates"])
    if not lons:
        return None
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def agregar_capas_base(mapa: folium.Map) -> None:
    """
    Capas base del mapa: OpenStreetMap (default) y vista satelital.

    La satelital usa Esri World Imagery, que es el servicio de ortofoto
    gratuito con cobertura y resolución útiles en México rural. Se declara su
    atribución, que es requisito de uso.
    """
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="Mapa (OpenStreetMap)",
        control=True,
    ).add_to(mapa)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics",
        name="Satélite",
        overlay=False,
        control=True,
        max_zoom=19,
    ).add_to(mapa)

    # Etiquetas de calles encima de la satelital: sin esto, en vista satelital
    # no se distinguen las calles y ubicar una manzana se vuelve adivinanza.
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Etiquetas sobre satélite",
        overlay=True,
        control=True,
        show=False,
    ).add_to(mapa)


def barra_pct(df_in, campo, titulo, orden=None, colores=None, altura=260):
    """Barra horizontal de distribución porcentual de una variable categórica."""
    if campo not in df_in.columns or df_in[campo].dropna().empty:
        return None
    cuenta = (df_in[campo].value_counts(normalize=True)
              .mul(100).round(1).reset_index())
    cuenta.columns = ["Respuesta", "Porcentaje"]
    if orden:
        cuenta["Respuesta"] = pd.Categorical(cuenta["Respuesta"],
                                             categories=orden, ordered=True)
        cuenta = cuenta.sort_values("Respuesta")
    else:
        cuenta = cuenta.sort_values("Porcentaje", ascending=True)

    fig = px.bar(
        cuenta, x="Porcentaje", y="Respuesta", orientation="h",
        color="Respuesta",
        color_discrete_sequence=colores or [VERDE_L, VERDE, AZUL_L, NARANJA,
                                            ROJO, AMARILLO, "#aaa"],
        text=cuenta["Porcentaje"].apply(lambda x: f"{x}%"),
        title=titulo, height=altura,
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(plot_bgcolor="white", paper_bgcolor="white", showlegend=False,
                      font_family="IBM Plex Sans",
                      margin=dict(t=45, b=5, l=200), xaxis_range=[0, 105])
    return fig


def grafica(fig, **kw):
    if fig:
        st.plotly_chart(fig, use_container_width=True, **kw)
    else:
        st.info("Sin datos suficientes para esta gráfica.")


def mapa_calor_cuotas(cuotas: pd.DataFrame, columna: str, titulo: str, escala: str):
    """Grid sexo x rango de edad como mapa de calor."""
    pivote = cuotas.pivot(index="rango_edad", columns="sexo", values=columna)
    pivote = pivote.reindex(ETIQUETAS_RANGO_EDAD)
    fig = px.imshow(
        pivote, text_auto=True, aspect="auto",
        color_continuous_scale=escala,
        labels=dict(x="Sexo", y="Rango de edad", color=columna),
        title=titulo, height=300,
    )
    fig.update_layout(font_family="IBM Plex Sans", margin=dict(t=50, b=10),
                      coloraxis_showscale=False)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — FILTROS
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ Filtros")
    st.markdown("---")

    estrato_sel = st.selectbox("Estrato", ["Todos"] + ESTRATOS)

    if estrato_sel == "Todos":
        secciones_disponibles = sorted(METAS_POR_SECCION.keys())
    else:
        secciones_disponibles = sorted(SECCIONES_POR_ESTRATO.get(estrato_sel, []))
    if _rol != "estatal":
        secciones_disponibles = [s for s in secciones_disponibles
                                 if s in _secciones_permitidas]

    seccion_sel = st.selectbox(
        "Sección electoral", ["Todas"] + secciones_disponibles,
        format_func=lambda s: "Todas" if s == "Todas"
        else f"{s} · {METAS_POR_SECCION.get(s, {}).get('estrato', '')}",
    )

    if not df_raw.empty and "fecha" in df_raw.columns:
        fechas_disponibles = sorted(df_raw["fecha"].dropna().unique().tolist())
    else:
        fechas_disponibles = []
    fecha_sel = st.multiselect(
        "Fecha de levantamiento", fechas_disponibles, default=fechas_disponibles,
        format_func=lambda d: d.strftime("%a %d-%b") if hasattr(d, "strftime") else str(d),
    )

    st.markdown("---")

    if not df_raw.empty:
        encuestadores = ["Todos"] + sorted(
            df_raw["encuestador_nombre"].dropna().unique().tolist()
        )
    else:
        encuestadores = ["Todos"]
    encuestador_sel = st.selectbox("Encuestador", encuestadores)

    solo_terminadas = st.checkbox(
        "Solo encuestas terminadas", value=False,
        help="El avance siempre se calcula con terminadas. Esto filtra las "
             "tablas de detalle y los tabulados del instrumento.",
    )

    st.markdown("---")

    if st.button("🔄 Actualizar datos", use_container_width=True):
        get_encuestas(PRIVATE_KEY, secciones=_filtro_secciones, force_refresh=True)
        st.session_state["ultimo_refresh"] = time.time()
        st.rerun()

    if ultima_actualizacion:
        hora_local = ultima_actualizacion.astimezone().strftime("%H:%M:%S")
        st.markdown(f'<div class="ts-badge">⏱ Actualizado: {hora_local}</div>',
                    unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"**Atlixco PIE** · L3 · 2026  \nInicio: {INICIO_OPERATIVO}")


# ══════════════════════════════════════════════════════════════════════════════
# APLICACIÓN DE FILTROS
# ══════════════════════════════════════════════════════════════════════════════
df = df_raw.copy()

if not df.empty:
    if estrato_sel != "Todos":
        df = df[df["estrato"] == estrato_sel]
    if seccion_sel != "Todas":
        df = df[df["seccion_electoral"] == seccion_sel]
    if fecha_sel and "fecha" in df.columns:
        df = df[df["fecha"].isin(fecha_sel)]
    if encuestador_sel != "Todos":
        df = df[df["encuestador_nombre"] == encuestador_sel]
    if solo_terminadas:
        df = df[df["terminada"]]


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
if MODO != "produccion":
    st.markdown(
        f'<div class="banner-test">⚠️ MODO {MODO.upper()} — datos de prueba, '
        f'sin corte por fecha de operativo. No usar para decisiones de campo.</div>',
        unsafe_allow_html=True,
    )

if info_carga.get("degradado"):
    st.warning(f"⚠️ {info_carga['mensaje']}")

titulo_geo = "Atlixco, Puebla"
if estrato_sel != "Todos":
    titulo_geo = f"Atlixco · estrato {estrato_sel}"
if seccion_sel != "Todas":
    titulo_geo = f"Atlixco · sección {seccion_sel}"

st.markdown(f"""
<div class="atx-header">
  <div style="font-size:2.1rem">📋</div>
  <div>
    <h1>Monitoreo y Auditoría de Encuestas — {titulo_geo}</h1>
    <p>Data &amp; AI Inclusion Technologies · L3 Atlixco ·
       {len(df):,} registros en la vista actual de {len(df_raw):,} del operativo</p>
  </div>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
if _rol == "estatal":
    tab0, tab1, tab2, tab3, tab4 = st.tabs([
        "🗂️  Planeación",
        "📈  Avance vs Meta",
        "🗺️  Mapa de Cobertura",
        "🚩  Flags de Calidad y Sesgo",
        "📊  Resultados del Instrumento",
    ])
else:
    tab0, tab1, tab2, tab3 = st.tabs([
        "🗂️  Planeación",
        "📈  Avance vs Meta",
        "🗺️  Mapa de Cobertura",
        "🚩  Flags de Calidad y Sesgo",
    ])
    tab4 = None


# ══════════════════════════════════════════════════════════════════════════════
# TAB 0 — PLANEACIÓN  (estático: sale de config.py, no toca Bubble)
# ══════════════════════════════════════════════════════════════════════════════
with tab0:
    st.markdown(
        f"""<div style="background:white;border-radius:9px;padding:16px 22px;
        box-shadow:0 2px 6px rgba(0,0,0,.07);margin-bottom:14px">
        <h3 style="margin:0;color:{AZUL};font-size:1.1rem">Diseño muestral del operativo</h3>
        <p style="margin:6px 0 0;color:#666;font-size:0.86rem">
        45 secciones electorales · 200 manzanas seleccionadas · 4 entrevistas por
        manzana · n = {META_GLOBAL} entrevistas objetivo</p></div>""",
        unsafe_allow_html=True,
    )

    p1, p2, p3, p4 = st.columns(4)
    kpi(p1, f"{len(METAS_POR_SECCION)}", "Secciones en muestra",
        f"{len(SECCIONES_FUERA_MUESTRA)} fuera de muestra")
    kpi(p2, f"{len(PUNTOS_MUESTRA)}", "Manzanas seleccionadas",
        "4 entrevistas por manzana", "azul")
    kpi(p3, f"{META_GLOBAL:,}", "Entrevistas objetivo", "meta global", "naranja")
    kpi(p4, f"{len(ESTRATOS)}", "Estratos",
        " · ".join(f"{e}: {META_POR_ESTRATO[e]}" for e in ESTRATOS))

    # ── Carga por estrato ─────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">Distribución de la muestra por estrato</div>',
                unsafe_allow_html=True)

    df_estrato = pd.DataFrame([
        {
            "Estrato": e,
            "Secciones": len(SECCIONES_POR_ESTRATO[e]),
            "Manzanas": sum(len(PUNTOS_POR_SECCION.get(s, []))
                            for s in SECCIONES_POR_ESTRATO[e]),
            "Meta": META_POR_ESTRATO[e],
            "% del total": round(100 * META_POR_ESTRATO[e] / META_GLOBAL, 1),
        }
        for e in ESTRATOS
    ]).sort_values("Meta", ascending=False)

    e1, e2 = st.columns([3, 2])
    with e1:
        fig_e = px.bar(
            df_estrato.sort_values("Meta"), x="Meta", y="Estrato", orientation="h",
            color="Estrato", color_discrete_map=ESTRATO_COLORES,
            text="Meta", title="Entrevistas objetivo por estrato", height=280,
        )
        fig_e.update_traces(textposition="outside")
        fig_e.update_layout(plot_bgcolor="white", paper_bgcolor="white",
                            showlegend=False, font_family="IBM Plex Sans",
                            margin=dict(t=45, b=10),
                            xaxis_range=[0, df_estrato["Meta"].max() * 1.18])
        st.plotly_chart(fig_e, use_container_width=True)
    with e2:
        tabla(df_estrato,
              estilos=[(lambda v: f"background-color:{GRIS_BG};font-weight:600",
                        ["Meta"])],
              formatos={"% del total": "{:.1f}%"})

    # ── Carga por sección ─────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">Carga de trabajo por sección</div>',
                unsafe_allow_html=True)

    df_plan = pd.DataFrame([
        {
            "Sección": s,
            "Estrato": v["estrato"],
            "Manzanas": v["manzanas"],
            "Localidades": v["localidades"],
            "Meta": v["meta_encuestas"],
            "LN muestreado": v["ln_mos"],
            "Observaciones": v.get("observaciones") or "",
        }
        for s, v in METAS_POR_SECCION.items()
    ]).sort_values("Meta", ascending=False)

    fig_sec = px.bar(
        df_plan.sort_values("Meta"), x="Meta", y="Sección", orientation="h",
        color="Estrato", color_discrete_map=ESTRATO_COLORES,
        title="Entrevistas objetivo por sección electoral",
        height=max(420, 17 * len(df_plan)),
    )
    fig_sec.update_layout(plot_bgcolor="white", paper_bgcolor="white",
                          font_family="IBM Plex Sans", margin=dict(t=45, b=10),
                          yaxis=dict(type="category"),
                          legend=dict(orientation="h", y=-0.04))
    st.plotly_chart(fig_sec, use_container_width=True)

    with st.expander("Ver tabla de planeación por sección", expanded=False):
        tabla(df_plan)
        st.caption(
            f"La sección 160 concentra {METAS_POR_SECCION[160]['meta_encuestas']} "
            f"entrevestas en {METAS_POR_SECCION[160]['manzanas']} manzanas: fue "
            f"reseccionada en 2026 (vigentes "
            f"{', '.join(map(str, SECCIONES_RESECCIONADAS[160]))}), pero campo "
            f"captura 160 y el monitoreo usa su polígono anterior."
            .replace("entrevestas", "entrevistas")
        )

    # ── Cuota objetivo ────────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">Cuota objetivo — sexo × rango de edad</div>',
                unsafe_allow_html=True)

    df_cuota_obj = pd.DataFrame([
        {"rango_edad": r, "sexo": s, "meta": CUOTAS_GLOBALES[(r, s)]}
        for r in ETIQUETAS_RANGO_EDAD for s in SEXOS_CUOTA
    ])
    c1, c2 = st.columns([2, 3])
    with c1:
        grafica(mapa_calor_cuotas(df_cuota_obj, "meta",
                                  "Entrevistas objetivo por celda", "Blues"))
    with c2:
        st.markdown(
            """Las cuotas por sección se derivan de una distribución única
            (`DISTRIBUCION_CUOTAS` en `config.py`) repartida por el método de
            mayores restos, de modo que las ocho celdas **siempre suman
            exactamente la meta de cada sección**.

            Recalibrar la cuota expost es cambiar ocho proporciones en un solo
            lugar: las 45 secciones se recalculan solas y siguen cuadrando.""")
        st.warning(
            "Las proporciones actuales son **placeholder** — una estructura de "
            "población adulta aproximada, no derivada del marco muestral de "
            "Atlixco. Calibrar antes de usarlas como cuota operativa.")

    # ── Enlaces de campo para compartir ───────────────────────────────────────
    # El brigadista no entra al tablero: abre un link en su celular. El
    # coordinador copia de aquí y manda por WhatsApp. Se usa st.code() porque
    # trae botón de copiar integrado — un st.text_area obligaría a seleccionar
    # a mano en el celular, que es donde esto se va a usar.
    st.markdown('<div class="sec-title">Enlaces de campo para compartir</div>',
                unsafe_allow_html=True)

    seccion_enlace = st.selectbox(
        "Sección a compartir", sorted(METAS_POR_SECCION.keys()),
        format_func=lambda s: (f"{s} · {METAS_POR_SECCION[s]['estrato']} · "
                               f"{METAS_POR_SECCION[s]['manzanas']} manzanas · "
                               f"meta {METAS_POR_SECCION[s]['meta_encuestas']}"),
        key="seccion_enlace_campo",
    )

    info_sec = METAS_POR_SECCION[seccion_enlace]
    puntos_sec = PUNTOS_POR_SECCION.get(seccion_enlace, [])

    # Mapas de Google Maps que cubren esta sección (lista vacía = todo el operativo)
    mapas_aplicables = [
        m for m in MAPAS_CAMPO
        if not m.get("secciones") or seccion_enlace in m["secciones"]
    ]

    ec1, ec2 = st.columns([3, 2])

    with ec1:
        lineas = [
            f"📍 ATLIXCO · SECCIÓN {seccion_enlace}",
            f"Estrato: {info_sec['estrato']} · "
            f"{info_sec['manzanas']} manzanas · meta {info_sec['meta_encuestas']} entrevistas",
            "",
        ]
        for m in mapas_aplicables:
            lineas += [f"🗺️ {m['etiqueta']}", m["url"], ""]

        lineas.append("Manzanas asignadas (4 entrevistas cada una):")
        for p in sorted(puntos_sec, key=lambda x: (x["localidad"], x["manzana"])):
            lineas.append(f"• Loc {p['localidad']} · Mz {p['manzana']} → "
                          f"{p.get('google_maps_url', '')}")
        if info_sec.get("observaciones"):
            lineas += ["", f"⚠️ {info_sec['observaciones']}"]

        st.code("\n".join(lineas), language=None)
        st.caption(
            "Usa el botón de copiar (esquina superior derecha del bloque) y "
            "pégalo en WhatsApp. Los enlaces por manzana abren Google Maps en "
            "las coordenadas exactas del punto a visitar.")

    with ec2:
        if mapas_aplicables:
            st.markdown("**Mapas que cubren esta sección**")
            for m in mapas_aplicables:
                alcance = ("todo el operativo" if not m.get("secciones")
                           else f"{len(m['secciones'])} secciones")
                st.markdown(f"- [{m['etiqueta']}]({m['url']}) · {alcance}")
        else:
            st.info(
                "No hay mapas de Google Maps cargados todavía. Pégalos en "
                "`MAPAS_CAMPO` (config.py) y aparecerán aquí junto a los "
                "enlaces por manzana.\n\n"
                "Mientras tanto, los enlaces por manzana ya son compartibles: "
                "salen de la tabla maestra y abren el punto exacto.")

        st.markdown("**Enlaces por manzana**")
        st.caption(
            f"{len(puntos_sec)} manzanas en esta sección · "
            f"{sum(1 for p in puntos_sec if p.get('google_maps_url'))} con enlace")

    with st.expander("Ver todos los enlaces por manzana del operativo"):
        df_enlaces = pd.DataFrame([
            {
                "Sección": p["seccion"],
                "Localidad": p["localidad"],
                "Manzana": p["manzana"],
                "Punto": p["punto_id"],
                "Objetivo": p["meta_punto"],
                "Google Maps": p.get("google_maps_url", ""),
            }
            for p in PUNTOS_MUESTRA
        ]).sort_values(["Sección", "Localidad", "Manzana"])
        st.dataframe(
            df_enlaces, use_container_width=True, hide_index=True, height=420,
            column_config={
                "Google Maps": st.column_config.LinkColumn(
                    "Google Maps", display_text="Abrir 📍")
            },
        )

    # ── Mapa de planeación ────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">Mapa de la muestra</div>',
                unsafe_allow_html=True)

    if GEOJSON is None:
        st.warning(f"No se encontró {GEOJSON_SECCIONES}. Colócalo junto a app.py.")
    else:
        geo_plan = copy.deepcopy(GEOJSON)
        for feature in geo_plan["features"]:
            sec = feature["properties"].get("seccion")
            info = METAS_POR_SECCION.get(sec)
            if info is None:
                feature["properties"].update(
                    {"estrato": "—", "meta": 0, "manzanas": 0,
                     "estado": "Fuera de muestra"})
            else:
                feature["properties"].update(
                    {"estrato": info["estrato"], "meta": info["meta_encuestas"],
                     "manzanas": info["manzanas"], "estado": "En muestra"})

        def estilo_plan(feature):
            props = feature["properties"]
            if props.get("estado") == "Fuera de muestra":
                return {"fillColor": "#CCCCCC", "color": "#999999",
                        "weight": 0.6, "fillOpacity": 0.3}
            return {"fillColor": ESTRATO_COLORES.get(props["estrato"], AZUL_L),
                    "color": "#333", "weight": 1.2, "fillOpacity": 0.5}

        mapa_plan = folium.Map(location=ATLIXCO_CENTRO, zoom_start=ATLIXCO_ZOOM,
                               tiles=None)
        agregar_capas_base(mapa_plan)

        folium.GeoJson(
            geo_plan, style_function=estilo_plan,
            highlight_function=lambda f: {"fillOpacity": 0.8, "weight": 2.5},
            tooltip=folium.GeoJsonTooltip(
                fields=["seccion", "estrato", "estado", "manzanas", "meta"],
                aliases=["Sección:", "Estrato:", "Estado:", "Manzanas:", "Meta:"],
                localize=True, sticky=True,
                style=("background-color:white;border:1px solid #2E7D5E;"
                       "border-radius:6px;padding:8px 12px;"
                       "font-family:'IBM Plex Sans',sans-serif;font-size:13px;"),
            ),
            name="Secciones electorales",
        ).add_to(mapa_plan)

        capa_manzanas = folium.FeatureGroup(name="Manzanas seleccionadas", show=True)
        for p in PUNTOS_MUESTRA:
            folium.CircleMarker(
                location=[p["latitud"], p["longitud"]], radius=4,
                color="#FFFFFF", weight=1.2,
                fill=True, fill_color=ESTRATO_COLORES.get(p["estrato"], AZUL),
                fill_opacity=0.95,
                tooltip=f"{p['punto_id']} · meta {p['meta_punto']}",
                popup=(f"<b>{p['punto_id']}</b><br>Sección {p['seccion']}<br>"
                       f"Localidad {p['localidad']} · Manzana {p['manzana']}<br>"
                       f"Objetivo: {p['meta_punto']} entrevistas"),
            ).add_to(capa_manzanas)
        capa_manzanas.add_to(mapa_plan)

        folium.LayerControl(collapsed=False).add_to(mapa_plan)
        st_folium(mapa_plan, use_container_width=True, height=540, returned_objects=[],
                  key="mapa_planeacion")
        st.caption(
            "Polígonos coloreados por estrato · puntos = las 200 manzanas de la "
            "muestra · gris = secciones fuera de muestra. Usa el control de capas "
            "para cambiar a vista satelital.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — AVANCE VS META
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    if df_raw.empty:
        st.info(
            "Sin datos todavía. El operativo inicia el "
            f"{INICIO_OPERATIVO}; las capturas previas se descartan por regla. "
            "Si esperabas ver registros, verifica la conexión a Bubble."
        )
    else:
        resumen = kpis.resumen_global(df)

        meta_vista = (
            META_GLOBAL if (estrato_sel == "Todos" and seccion_sel == "Todas")
            else (METAS_POR_SECCION[seccion_sel]["meta_encuestas"]
                  if seccion_sel != "Todas" else META_POR_ESTRATO[estrato_sel])
        )
        terminadas_vista = int(df["terminada"].sum())
        pct_vista = round(100 * terminadas_vista / meta_vista, 1) if meta_vista else 0.0

        a1, a2, a3, a4 = st.columns(4)
        kpi(a1, f"{len(df):,}", "Encuestas levantadas",
            f"{resumen['incompletas']:,} sin terminar")
        kpi(a2, f"{terminadas_vista:,}", "Encuestas terminadas",
            f"criterio: {CRITERIO_TERMINADA.replace('_', ' ')}", "azul")
        kpi(a3, f"{meta_vista:,}", "Meta de la vista",
            f"meta global: {META_GLOBAL:,}", "naranja")
        kpi(a4, f"{pct_vista}%", "Avance vs meta",
            f"faltan {max(meta_vista - terminadas_vista, 0):,}",
            "rojo" if pct_vista < 60 else ("amarillo" if pct_vista < 100 else ""))

        duracion_prom = round(df["duracion_min"].mean(), 1) if len(df) else 0
        st.caption(
            f"Duración promedio de entrevista: **{duracion_prom}′** "
            f"(rango esperado {DUR_MIN_MIN}–{DUR_MAX_MIN} min)")

        # ── Ritmo diario ──────────────────────────────────────────────────────
        diario = kpis.avance_diario(df)
        if len(diario) > 1:
            st.markdown('<div class="sec-title">Ritmo de levantamiento</div>',
                        unsafe_allow_html=True)
            fig_d = px.bar(diario, x="dia", y="terminadas",
                           color_discrete_sequence=[VERDE],
                           title="Encuestas terminadas por día", height=260,
                           labels={"dia": "", "terminadas": "Terminadas"})
            fig_d.add_scatter(x=diario["dia"], y=diario["acumulado"],
                              mode="lines+markers", name="Acumulado",
                              line=dict(color=AZUL_L, width=2), yaxis="y2")
            fig_d.update_layout(
                plot_bgcolor="white", paper_bgcolor="white",
                font_family="IBM Plex Sans", margin=dict(t=45, b=10),
                yaxis2=dict(overlaying="y", side="right", title="Acumulado"),
                legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig_d, use_container_width=True)

        # ── Avance por sección ────────────────────────────────────────────────
        st.markdown('<div class="sec-title">Avance por sección electoral</div>',
                    unsafe_allow_html=True)

        av_sec = kpis.avance_por_seccion(df)
        if estrato_sel != "Todos":
            av_sec = av_sec[av_sec["estrato"] == estrato_sel]
        if seccion_sel != "Todas":
            av_sec = av_sec[av_sec["seccion"] == seccion_sel]

        st.caption(
            f"{int(av_sec['cubierta'].sum())} de {len(av_sec)} secciones en vista "
            f"alcanzaron su meta · "
            f"{int(av_sec['manzanas_sin_cobertura'].sum())} manzanas sin ninguna "
            f"encuesta todavía")

        tbl_sec = av_sec[["seccion", "estrato", "meta", "avance", "pct", "faltan",
                          "manzanas", "manzanas_cubiertas", "manzanas_sin_cobertura",
                          "semaforo"]].copy()
        tbl_sec.columns = ["Sección", "Estrato", "Meta", "Avance", "% Avance",
                           "Faltan", "Manzanas", "Cubiertas", "Sin cobertura",
                           "Semáforo"]
        tabla(tbl_sec,
              estilos=[(color_pct, ["% Avance"]), (color_semaforo, ["Semáforo"])],
              formatos={"% Avance": "{:.1f}%"},
              altura=min(80 + len(tbl_sec) * 35, 520))
        st.caption("🟢 ≥100% de meta · 🟡 60–99% · 🔴 <60%")

        # ── Avance por estrato ────────────────────────────────────────────────
        if estrato_sel == "Todos" and seccion_sel == "Todas":
            st.markdown('<div class="sec-title">Avance por estrato</div>',
                        unsafe_allow_html=True)
            av_est = kpis.avance_por_estrato(df)
            tbl_est = av_est[["estrato", "n_secciones", "meta", "avance", "pct",
                              "faltan", "semaforo"]].copy()
            tbl_est.columns = ["Estrato", "Secciones", "Meta", "Avance",
                               "% Avance", "Faltan", "Semáforo"]
            tabla(tbl_est,
                  estilos=[(color_pct, ["% Avance"]),
                           (color_semaforo, ["Semáforo"])],
                  formatos={"% Avance": "{:.1f}%"})

        # ── Avance por encuestador ────────────────────────────────────────────
        st.markdown('<div class="sec-title">Avance por encuestador</div>',
                    unsafe_allow_html=True)
        av_enc = kpis.avance_por_encuestador(df)
        tbl_enc = av_enc[["encuestador_nombre", "levantadas", "terminadas",
                          "pct_completitud", "duracion_prom_min",
                          "duracion_mediana_min", "secciones_trabajadas",
                          "manzanas_trabajadas", "dias_activos"]].copy()
        tbl_enc.columns = ["Encuestador", "Levantadas", "Terminadas",
                           "% Completitud", "Dur. prom", "Dur. mediana",
                           "Secciones", "Manzanas", "Días activos"]
        tabla(tbl_enc,
              estilos=[(color_duracion, ["Dur. prom", "Dur. mediana"]),
                       (color_pct, ["% Completitud"])],
              formatos={"Dur. prom": "{:.1f}", "Dur. mediana": "{:.1f}",
                        "% Completitud": "{:.1f}%"},
              altura=min(80 + len(tbl_enc) * 35, 420))
        st.caption(
            f"La tasa de completitud separa a quien levanta mucho y termina poco "
            f"—patrón distinto al de quien va lento. Meta diaria de referencia: "
            f"{META_ENCUESTAS_DIARIAS} encuestas.")

        with st.expander("📊 Distribución de duraciones", expanded=False):
            fig_box = px.box(df, y="duracion_min", color_discrete_sequence=[VERDE],
                             labels={"duracion_min": "Minutos"},
                             title="Duración de entrevistas")
            fig_box.add_hline(y=DUR_MAX_MIN, line_dash="dash", line_color=AMARILLO,
                              annotation_text=f"Máx ({DUR_MAX_MIN} min)")
            fig_box.add_hline(y=DUR_MIN_MIN, line_dash="dot", line_color=ROJO,
                              annotation_text=f"Mín ({DUR_MIN_MIN} min)")
            fig_box.update_layout(plot_bgcolor="white", paper_bgcolor="white",
                                  font_family="IBM Plex Sans",
                                  margin=dict(t=50, b=10))
            st.plotly_chart(fig_box, use_container_width=True)

        # ── Cuotas ────────────────────────────────────────────────────────────
        st.markdown('<div class="sec-title">Cobertura de cuotas — sexo × edad</div>',
                    unsafe_allow_html=True)

        if seccion_sel != "Todas":
            cuotas = kpis.avance_cuotas(df, seccion_sel)
        else:
            cuotas = kpis.avance_cuotas_global(df)

        q1, q2 = st.columns([2, 3])
        with q1:
            grafica(mapa_calor_cuotas(cuotas, "pct", "% de cuota alcanzado", "RdYlGn"))
        with q2:
            tbl_q = cuotas[["rango_edad", "sexo", "meta", "avance", "pct",
                            "faltan", "semaforo"]].copy()
            tbl_q.columns = ["Rango de edad", "Sexo", "Meta", "Avance",
                             "% Cuota", "Faltan", "Semáforo"]
            tabla(tbl_q,
                  estilos=[(color_pct, ["% Cuota"]),
                           (color_semaforo, ["Semáforo"])],
                  formatos={"% Cuota": "{:.1f}%"})

        st.markdown(
            '<div class="sec-title">Sesgo de composición (no de avance)</div>',
            unsafe_allow_html=True)
        sesgo = kpis.sesgo_cuotas(df)
        tbl_sesgo = sesgo[["rango_edad", "sexo", "pct_objetivo", "pct_logrado",
                           "desviacion_pp", "faltan"]].copy()
        tbl_sesgo.columns = ["Rango de edad", "Sexo", "% objetivo", "% logrado",
                             "Desviación (pp)", "Faltan"]
        tabla(tbl_sesgo,
              estilos=[(color_desviacion, ["Desviación (pp)"])],
              formatos={"% objetivo": "{:.1f}%", "% logrado": "{:.1f}%",
                        "Desviación (pp)": "{:+.1f}"})
        st.caption(
            "Compara la composición **lograda** contra la **objetivo**, "
            "independientemente del avance. Una cuota al 50% pareja en las ocho "
            "celdas no tiene sesgo; una al 90% en jóvenes y 10% en adultos "
            "mayores tiene el mismo avance global y un sesgo que hay que "
            "corregir mientras aún se puede. 🔴 ≥10 pp · 🟡 5–10 pp")

        # ── Manzanas sin cobertura ────────────────────────────────────────────
        st.markdown('<div class="sec-title">Manzanas sin cobertura</div>',
                    unsafe_allow_html=True)
        sin_cob = kpis.manzanas_sin_cobertura(df)
        if seccion_sel != "Todas":
            sin_cob = sin_cob[sin_cob["seccion"] == seccion_sel]
        if sin_cob.empty:
            st.success("Todas las manzanas de la vista tienen al menos una encuesta.")
        else:
            st.caption(
                f"{len(sin_cob)} manzanas de la muestra sin ninguna encuesta "
                f"terminada. Es el dato más accionable para decidir dónde "
                f"desplegar gente.")
            tbl_m = sin_cob[["punto_id", "seccion", "estrato", "localidad",
                             "manzana", "meta_punto"]].copy()
            tbl_m.columns = ["Punto", "Sección", "Estrato", "Localidad",
                             "Manzana", "Objetivo"]
            tabla(tbl_m, altura=min(80 + len(tbl_m) * 35, 420))


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — MAPA DE COBERTURA
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="sec-title">Cobertura por sección y manzana</div>',
                unsafe_allow_html=True)

    if GEOJSON is None:
        st.warning(f"No se encontró {GEOJSON_SECCIONES}. Colócalo junto a app.py.")
    elif df_raw.empty:
        st.info("Sin datos disponibles todavía.")
    else:
        # El mapa respeta estrato y fecha, pero NO el filtro de sección: así el
        # coropleto muestra el avance real de las 45 y el selector de abajo solo
        # controla zoom y resaltado.
        df_mapa = df_raw.copy()
        if estrato_sel != "Todos":
            df_mapa = df_mapa[df_mapa["estrato"] == estrato_sel]
        if fecha_sel and "fecha" in df_mapa.columns:
            df_mapa = df_mapa[df_mapa["fecha"].isin(fecha_sel)]

        av_todas = kpis.avance_por_seccion(df_mapa)
        lookup = av_todas.set_index("seccion").to_dict(orient="index")

        zoom_sel = st.selectbox(
            "🔍 Resaltar sección", ["Todas"] + sorted(av_todas["seccion"].tolist()),
            format_func=lambda s: "Todas" if s == "Todas"
            else f"{s} · {METAS_POR_SECCION.get(s, {}).get('estrato', '')}",
            key="zoom_seccion_mapa",
        )

        geo = copy.deepcopy(GEOJSON)
        bounds_zoom = None
        for feature in geo["features"]:
            sec = feature["properties"].get("seccion")
            info = lookup.get(sec)
            if info is None:
                feature["properties"].update(
                    {"meta": 0, "avance": 0, "pct": 0.0, "estrato": "—",
                     "estado": "Fuera de muestra", "sin_cobertura": 0})
            else:
                feature["properties"].update({
                    "meta": int(info["meta"]), "avance": int(info["avance"]),
                    "pct": float(info["pct"]), "estrato": info["estrato"],
                    "sin_cobertura": int(info["manzanas_sin_cobertura"]),
                    "estado": ("🟢 En meta" if info["pct"] >= 100
                               else "🟡 En riesgo" if info["pct"] >= 60
                               else "🔴 Bajo meta"),
                })
            if zoom_sel != "Todas" and sec == zoom_sel:
                bounds_zoom = bounds_de_geometria(feature["geometry"])

        def estilo_seccion(feature):
            props = feature["properties"]
            resaltada = zoom_sel != "Todas" and props.get("seccion") == zoom_sel
            if props.get("estado") == "Fuera de muestra":
                base = {"fillColor": "#CCCCCC", "color": "#999999",
                        "weight": 0.6, "fillOpacity": 0.3}
            else:
                pct = props.get("pct", 0)
                if pct >= 100:
                    base = {"fillColor": VERDE, "color": "#1A5C42",
                            "weight": 1.3, "fillOpacity": 0.7}
                elif pct >= 60:
                    base = {"fillColor": AMARILLO, "color": "#8a6200",
                            "weight": 1.3, "fillOpacity": 0.7}
                else:
                    base = {"fillColor": ROJO, "color": "#7b1a14",
                            "weight": 1.3, "fillOpacity": 0.7}
            if resaltada:
                base = {**base, "color": AZUL, "weight": 4}
            elif zoom_sel != "Todas":
                base = {**base, "fillOpacity": base["fillOpacity"] * 0.3}
            return base

        mapa = folium.Map(location=ATLIXCO_CENTRO, zoom_start=ATLIXCO_ZOOM, tiles=None)
        agregar_capas_base(mapa)

        folium.GeoJson(
            geo, style_function=estilo_seccion,
            highlight_function=lambda f: {"fillOpacity": 0.92, "weight": 2.5,
                                          "color": AZUL},
            tooltip=folium.GeoJsonTooltip(
                fields=["seccion", "estrato", "estado", "avance", "meta", "pct",
                        "sin_cobertura"],
                aliases=["Sección:", "Estrato:", "Estado:", "Avance:", "Meta:",
                         "% avance:", "Manzanas sin cobertura:"],
                localize=True, sticky=True,
                style=("background-color:white;border:1px solid #2E7D5E;"
                       "border-radius:6px;padding:8px 12px;"
                       "font-family:'IBM Plex Sans',sans-serif;font-size:13px;"
                       "box-shadow:0 2px 8px rgba(0,0,0,0.15);"),
            ),
            name="Secciones electorales",
        ).add_to(mapa)

        # ── Manzanas objetivo ─────────────────────────────────────────────────
        cob_manzanas = kpis.cobertura_manzanas(
            df_mapa, None if zoom_sel == "Todas" else zoom_sel)
        capa_obj = folium.FeatureGroup(name="Manzanas objetivo", show=True)
        for _, m in cob_manzanas.iterrows():
            color = (VERDE if m["pct"] >= 100
                     else AMARILLO if m["avance"] > 0 else ROJO)
            folium.CircleMarker(
                location=[m["latitud"], m["longitud"]], radius=5,
                color="#FFFFFF", weight=1.4, fill=True, fill_color=color,
                fill_opacity=0.95,
                tooltip=f"{m['punto_id']} · {m['avance']}/{m['meta_punto']}",
                popup=(f"<b>{m['punto_id']}</b><br>Sección {m['seccion']}<br>"
                       f"Avance: {m['avance']} de {m['meta_punto']}"),
            ).add_to(capa_obj)
        capa_obj.add_to(mapa)

        # ── Encuestas levantadas ──────────────────────────────────────────────
        df_puntos = (df_mapa if zoom_sel == "Todas"
                     else df_mapa[df_mapa["seccion_electoral"] == zoom_sel])
        con_coords = df_puntos["latitud"].notna() & df_puntos["longitud"].notna()
        df_geo = df_puntos[con_coords]

        capa_enc = folium.FeatureGroup(name="Encuestas levantadas", show=True)
        for _, r in df_geo.iterrows():
            nivel = r.get("flag_georef_nivel")
            color = (ROJO if nivel == "rojo"
                     else NARANJA if nivel == "amarillo" else AZUL_L)
            dist = r.get("distancia_punto_m")
            dist_txt = f"{dist:,.0f} m" if pd.notna(dist) else "—"
            folium.CircleMarker(
                location=[r["latitud"], r["longitud"]], radius=3,
                color=color, fill=True, fill_opacity=0.8, weight=1,
                tooltip=f"{r.get('encuestador_nombre', '—')} · {dist_txt}",
                popup=(f"<b>{r.get('encuestador_nombre', '—')}</b><br>"
                       f"Folio {r['folio']}<br>"
                       f"Sección declarada: {r['seccion_electoral']}<br>"
                       f"Sección por coordenadas: {r.get('seccion_georef')}<br>"
                       f"Manzana asignada: {r.get('punto_asignado')}<br>"
                       f"Distancia a la manzana: {dist_txt}"),
            ).add_to(capa_enc)
        capa_enc.add_to(mapa)

        folium.LayerControl(collapsed=False).add_to(mapa)
        if bounds_zoom:
            mapa.fit_bounds(bounds_zoom)

        st_folium(mapa, use_container_width=True, height=560, returned_objects=[],
                  key="mapa_cobertura")

        st.caption(
            "**Secciones:** 🟢 ≥100% · 🟡 60–99% · 🔴 <60% · ⬜ fuera de muestra. "
            "**Manzanas objetivo:** 🟢 completa · 🟡 iniciada · 🔴 sin cobertura. "
            "**Encuestas:** 🔵 dentro de su sección · 🟠 en otra sección de "
            "Atlixco · 🔴 fuera de Atlixco. Cambia a vista satelital con el "
            "control de capas, arriba a la derecha.")

        # ── Diagnóstico de georreferenciación ─────────────────────────────────
        st.markdown('<div class="sec-title">Diagnóstico de georreferenciación</div>',
                    unsafe_allow_html=True)

        n_total = len(df_puntos)
        n_con = int(con_coords.sum())
        n_sin = n_total - n_con
        n_rojo = int((df_geo.get("flag_georef_nivel") == "rojo").sum()) if n_con else 0
        n_amar = int((df_geo.get("flag_georef_nivel") == "amarillo").sum()) if n_con else 0

        g1, g2, g3, g4 = st.columns(4)
        kpi(g1, f"{n_con:,}", "Con coordenadas",
            f"{round(100 * n_con / max(n_total, 1), 1)}% de {n_total:,}")
        kpi(g2, f"{n_sin:,}", "Sin coordenadas",
            f"{round(100 * n_sin / max(n_total, 1), 1)}%",
            "amarillo" if n_sin else "")
        kpi(g3, f"{n_amar:,}", "En otra sección", "flag amarillo",
            "amarillo" if n_amar else "")
        kpi(g4, f"{n_rojo:,}", "Fuera de Atlixco", "flag rojo",
            "rojo" if n_rojo else "")

        if n_con:
            dist = df_geo["distancia_punto_m"].dropna()
            if len(dist):
                st.caption(
                    f"Distancia a la manzana asignada — mediana "
                    f"**{dist.median():,.0f} m** · p90 **{dist.quantile(.9):,.0f} m** "
                    f"· máx **{dist.max():,.0f} m**. Es diagnóstico, no genera "
                    f"alerta: hay manzanas con pocas viviendas donde el "
                    f"encuestador legítimamente tiene que moverse. Referencia "
                    f"visual: {DIST_MANZANA_REFERENCIA_M} m.")

        df_fuera = df_geo[df_geo["flag_georef_nivel"].isin(["amarillo", "rojo"])]
        if not df_fuera.empty:
            st.markdown(
                '<div class="sec-title">Encuestadores con puntos fuera de zona</div>',
                unsafe_allow_html=True)
            resumen_enc = (
                df_fuera.groupby("encuestador_nombre").agg(
                    rojo=("flag_georef_nivel", lambda s: int((s == "rojo").sum())),
                    amarillo=("flag_georef_nivel",
                              lambda s: int((s == "amarillo").sum())),
                    secciones=("seccion_electoral",
                               lambda s: sorted({int(x) for x in s.dropna()})),
                    folios=("folio", lambda s: ", ".join(map(str, s))),
                ).reset_index().sort_values(["rojo", "amarillo"], ascending=False)
            )
            resumen_enc.columns = ["Encuestador", "🔴 Fuera de Atlixco",
                                   "🟠 Otra sección", "Secciones declaradas",
                                   "Folios"]
            tabla(resumen_enc, altura=min(80 + len(resumen_enc) * 35, 320))


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — FLAGS DE CALIDAD Y SESGO
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    if df.empty:
        st.info("Sin registros para los filtros seleccionados.")
    else:
        cols_nivel = [c for c in df.columns
                      if c.startswith("flag_") and c.endswith("_nivel")]

        n_con_flag = int((df["flags_rojo_n"] + df["flags_amarillo_n"] > 0).sum())
        n_rojo_tot = int((df["flags_rojo_n"] > 0).sum())

        f1, f2, f3 = st.columns(3)
        kpi(f1, f"{n_con_flag:,}", "Registros con ≥1 flag",
            f"{round(100 * n_con_flag / max(len(df), 1), 1)}% de la vista",
            "amarillo" if n_con_flag else "")
        kpi(f2, f"{n_rojo_tot:,}", "Con flag rojo",
            "revisión prioritaria", "rojo" if n_rojo_tot else "")
        kpi(f3, f"{len(df):,}", "Registros en vista", "")

        # ── Resumen por tipo de flag ──────────────────────────────────────────
        st.markdown('<div class="sec-title">Flags por tipo</div>',
                    unsafe_allow_html=True)

        resumen_flags = pd.DataFrame([
            {
                "flag": c.replace("flag_", "").replace("_nivel", "")
                         .replace("_", " ").title(),
                "rojo": int((df[c] == "rojo").sum()),
                "amarillo": int((df[c] == "amarillo").sum()),
                "sin_flag": int((df[c].isna()).sum()),
                "pct_afectado": round(
                    100 * int(((df[c] == "rojo") | (df[c] == "amarillo")).sum())
                    / max(len(df), 1), 1),
            }
            for c in cols_nivel
        ])

        fig_flags = px.bar(
            resumen_flags.sort_values("pct_afectado"),
            y="flag", x=["rojo", "amarillo", "sin_flag"], orientation="h",
            color_discrete_map={"rojo": ROJO, "amarillo": AMARILLO,
                                "sin_flag": "#E3E7EC"},
            labels={"value": "Registros", "flag": "", "variable": ""},
            title="Registros marcados por tipo de flag",
            height=max(260, 62 * len(resumen_flags)),
        )
        fig_flags.update_layout(
            barmode="stack", plot_bgcolor="white", paper_bgcolor="white",
            font_family="IBM Plex Sans", margin=dict(t=45, b=10, l=10),
            legend_title_text="", legend=dict(orientation="h", y=-0.12))
        fig_flags.for_each_trace(lambda t: t.update(
            name={"rojo": "Rojo", "amarillo": "Amarillo",
                  "sin_flag": "Sin flag"}.get(t.name, t.name)))
        st.plotly_chart(fig_flags, use_container_width=True)

        with st.expander("Ver conteos por flag"):
            tbl_rf = resumen_flags[["flag", "rojo", "amarillo",
                                    "pct_afectado"]].copy()
            tbl_rf.columns = ["Flag", "🔴 Rojo", "🟡 Amarillo", "% afectado"]
            tabla(tbl_rf, formatos={"% afectado": "{:.1f}%"})

        # ── Sesgo por encuestador ─────────────────────────────────────────────
        st.markdown(
            f'<div class="sec-title">Sesgo hacia '
            f'{CANDIDATOS[CANDIDATO_FOCO]} — por encuestador</div>',
            unsafe_allow_html=True)

        sesgo_enc = flags.flag_sesgo_encuestador(df)
        if sesgo_enc.empty:
            st.info("Sin registros evaluables todavía.")
        else:
            tasa_global = sesgo_enc["tasa_global"].iloc[0]
            st.caption(
                f"Tasa global del operativo: **{tasa_global:.1%}** de los "
                f"registros evalúan mejor a {CANDIDATOS[CANDIDATO_FOCO]} que al "
                f"promedio de los demás aspirantes que el entrevistado conoce. "
                f"La comparación es contra esa tasa, no contra una expectativa "
                f"externa: si Atlixco entero favorece al aspirante, eso sube la "
                f"tasa global y nadie se marca por ello. Lo que destaca es quien "
                f"se aparta de sus propios compañeros.")

            tbl_sesgo = sesgo_enc[["encuestador_nombre", "n_evaluables",
                                   "n_pro_foco", "tasa_pro_foco", "score_prom",
                                   "desvio_sigmas",
                                   "flag_sesgo_encuestador_nivel"]].copy()
            tbl_sesgo.columns = ["Encuestador", "Evaluables", "Pro-aspirante",
                                 "Tasa", "Score prom.", "Desvío (σ)", "Nivel"]
            tabla(tbl_sesgo,
                  estilos=[(color_semaforo, ["Nivel"])],
                  formatos={"Tasa": "{:.1%}", "Score prom.": "{:+.2f}",
                            "Desvío (σ)": "{:+.2f}"},
                  altura=min(80 + len(tbl_sesgo) * 35, 360))
            st.caption(
                "La prueba es de una cola: solo se marca la desviación **alta**. "
                "Evaluar poco favorablemente al aspirante no es una anomalía de "
                "calidad, es una opinión. Score positivo = el aspirante foco "
                "mejor evaluado que el resto.")

        # ── Rachas de capturas rápidas ────────────────────────────────────────
        st.markdown('<div class="sec-title">Rachas de capturas rápidas</div>',
                    unsafe_allow_html=True)
        rachas = flags.flag_racha_rapida(df)
        if rachas.empty:
            st.success("Sin rachas de capturas rápidas detectadas.")
        else:
            tbl_r = rachas.copy()
            tbl_r.columns = ["Encuestador", "Día", "Encuestas", "Dur. prom",
                             "Desde", "Hasta", "Folios"]
            tabla(tbl_r, formatos={"Dur. prom": "{:.1f}"},
                  altura=min(80 + len(tbl_r) * 35, 320))
            st.caption(
                "Una encuesta rápida aislada es plausible —alguien eficiente, o "
                "un entrevistado que no conoce a ningún aspirante y se salta "
                "medio cuestionario por los filtros. Lo que amerita revisión es "
                "la racha seguida.")

        # ── Detalle de registros marcados ─────────────────────────────────────
        st.markdown('<div class="sec-title">Registros con flags activos</div>',
                    unsafe_allow_html=True)

        opciones_tipo = ["Todos"] + [
            c.replace("flag_", "").replace("_nivel", "").replace("_", " ").title()
            for c in cols_nivel
        ]
        d1, d2 = st.columns(2)
        with d1:
            tipo_sel = st.selectbox("Tipo de flag", opciones_tipo)
        with d2:
            nivel_sel = st.selectbox("Nivel", ["Todos", "rojo", "amarillo"])

        df_flags = df[df["flags_rojo_n"] + df["flags_amarillo_n"] > 0].copy()

        if tipo_sel != "Todos":
            col_obj = "flag_" + tipo_sel.lower().replace(" ", "_") + "_nivel"
            if col_obj in df_flags.columns:
                df_flags = df_flags[df_flags[col_obj].notna()]
                if nivel_sel != "Todos":
                    df_flags = df_flags[df_flags[col_obj] == nivel_sel]
        elif nivel_sel != "Todos":
            mascara = pd.Series(False, index=df_flags.index)
            for c in cols_nivel:
                mascara |= (df_flags[c] == nivel_sel)
            df_flags = df_flags[mascara]

        cols_mostrar = [c for c in (
            ["folio", "seccion_electoral", "estrato", "encuestador_nombre",
             "duracion_min", "score_favorabilidad", "distancia_punto_m",
             "flags_rojo_n", "flags_amarillo_n"] + cols_nivel
        ) if c in df_flags.columns]

        st.dataframe(
            df_flags[cols_mostrar].sort_values(
                ["flags_rojo_n", "flags_amarillo_n"], ascending=False),
            use_container_width=True, hide_index=True,
            height=min(80 + len(df_flags) * 35, 460),
        )
        st.caption(
            f"{len(df_flags):,} de {len(df):,} registros en vista tienen al "
            f"menos un flag activo.")

        # ── Cobertura desbalanceada ───────────────────────────────────────────
        st.markdown('<div class="sec-title">Cobertura desbalanceada por sección</div>',
                    unsafe_allow_html=True)
        cobertura = flags.resumen_cobertura_desbalanceada(df_raw)
        marcadas = cobertura[cobertura["flag_cobertura"]]
        if marcadas.empty:
            st.success("Sin secciones con cobertura desbalanceada.")
        else:
            tbl_c = marcadas[["seccion", "estrato", "meta", "avance", "pct",
                              "manzanas_sin_cobertura",
                              "flag_cobertura_nivel"]].copy()
            tbl_c.columns = ["Sección", "Estrato", "Meta", "Avance", "% Avance",
                             "Manzanas sin cobertura", "Nivel"]
            tabla(tbl_c,
                  estilos=[(color_semaforo, ["Nivel"])],
                  formatos={"% Avance": "{:.1f}%"},
                  altura=min(80 + len(tbl_c) * 35, 400))
            st.caption(
                "🔴 sin ningún avance habiendo avance en otras secciones · "
                "🟡 por debajo de la mitad del promedio global.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — RESULTADOS DEL INSTRUMENTO  (solo rol estatal)
# ══════════════════════════════════════════════════════════════════════════════
if tab4 is not None:
    with tab4:
        df_res = df[df["terminada"]] if not df.empty else df

        if df_res.empty:
            st.info("Sin encuestas terminadas para los filtros seleccionados.")
        else:
            st.caption(
                f"Tabulados sobre **{len(df_res):,} encuestas terminadas**. "
                f"Resultados preliminares sin ponderar — no usar para difusión.")

            # ── Perfil sociodemográfico ───────────────────────────────────────
            st.markdown('<div class="sec-title">Perfil sociodemográfico</div>',
                        unsafe_allow_html=True)
            s1, s2, s3 = st.columns(3)
            with s1:
                grafica(barra_pct(df_res, "sexo", "Sexo"))
            with s2:
                grafica(barra_pct(df_res, "rango_edad_cuota", "Rango de edad",
                                  orden=ETIQUETAS_RANGO_EDAD))
            with s3:
                grafica(barra_pct(df_res, "ine", "¿Cuenta con INE?",
                                  orden=["Sí", "No"], colores=[VERDE, "#ccc"]))

            # Pirámide de edades
            df_pir = df_res.dropna(subset=["edad", "sexo_cuota"]).copy()
            if len(df_pir):
                df_pir["grupo"] = df_pir["rango_edad_cuota"]
                pir = (df_pir.groupby(["grupo", "sexo_cuota"], observed=True)
                       .size().reset_index(name="n"))
                pir.loc[pir["sexo_cuota"] == "H", "n"] *= -1
                if not pir.empty:
                    maximo = int(pir["n"].abs().max())
                    paso = max(maximo // 4, 1)
                    pos = list(range(0, maximo + paso, paso))
                    ticks = [-t for t in reversed(pos[1:])] + pos
                    fig_p = px.bar(
                        pir, x="n", y="grupo", color="sexo_cuota", orientation="h",
                        color_discrete_map={"H": AZUL_L, "M": NARANJA},
                        category_orders={"grupo": ETIQUETAS_RANGO_EDAD},
                        labels={"n": "Entrevistas", "grupo": "", "sexo_cuota": ""},
                        title="Pirámide de edades (H izquierda · M derecha)",
                        height=300)
                    fig_p.update_xaxes(tickvals=ticks,
                                       ticktext=[abs(t) for t in ticks])
                    fig_p.update_layout(plot_bgcolor="white", paper_bgcolor="white",
                                        font_family="IBM Plex Sans",
                                        margin=dict(t=45, b=10))
                    st.plotly_chart(fig_p, use_container_width=True)

            st.markdown("---")

            # ── Identificación partidaria e intención de voto ─────────────────
            st.markdown(
                '<div class="sec-title">Afinidad partidista e intención de voto</div>',
                unsafe_allow_html=True)
            v1, v2 = st.columns(2)
            with v1:
                grafica(barra_pct(df_res, "identificacion_partidaria",
                                  "P4. Identificación partidista", altura=300))
            with v2:
                grafica(barra_pct(df_res, "intencion_voto_principal",
                                  "P5. Intención de voto", altura=300))

            st.markdown("---")

            # ── Conocimiento y opinión ────────────────────────────────────────
            st.markdown(
                '<div class="sec-title">Conocimiento y opinión de aspirantes</div>',
                unsafe_allow_html=True)

            filas_conoce = []
            for clave, nombre in CANDIDATOS.items():
                col = f"conocimiento_{clave}"
                if col in df_res.columns:
                    sub = df_res[col].dropna()
                    if len(sub):
                        pct = round(
                            sub.astype(str).str.strip().str.lower()
                            .isin(["sí", "si"]).mean() * 100, 1)
                        filas_conoce.append({"Aspirante": nombre,
                                             "% lo conoce": pct})

            k1, k2 = st.columns(2)
            with k1:
                if filas_conoce:
                    df_conoce = pd.DataFrame(filas_conoce).sort_values("% lo conoce")
                    fig_k = px.bar(
                        df_conoce, x="% lo conoce", y="Aspirante", orientation="h",
                        text=df_conoce["% lo conoce"].apply(lambda v: f"{v}%"),
                        color_discrete_sequence=[VERDE_L],
                        title="% que conoce a cada aspirante", height=330)
                    fig_k.update_traces(textposition="outside")
                    fig_k.update_layout(plot_bgcolor="white", paper_bgcolor="white",
                                        showlegend=False,
                                        font_family="IBM Plex Sans",
                                        margin=dict(t=45, b=5, l=170),
                                        xaxis_range=[0, 105])
                    st.plotly_chart(fig_k, use_container_width=True)
                else:
                    st.info("Sin datos de conocimiento todavía.")
            with k2:
                sel = st.selectbox(
                    "Aspirante — detalle", list(CANDIDATOS.items()),
                    format_func=lambda kv: kv[1], key="aspirante_sel")
                clave_sel, nombre_sel = sel
                grafica(barra_pct(
                    df_res, f"opinion_{clave_sel}",
                    f"Opinión sobre {nombre_sel}",
                    orden=["Buena", "Regular", "Mala"],
                    colores=[VERDE, AMARILLO, ROJO], altura=330))
                st.caption(
                    "La opinión solo se pregunta a quien declaró conocer al "
                    "aspirante — la base es menor que el total de entrevistas.")

            # ── Atributos ─────────────────────────────────────────────────────
            st.markdown(f"**Evaluación de atributos — {nombre_sel}**")
            columnas_atrib = st.columns(3)
            for i, atributo in enumerate(ATRIBUTOS_BLOQUE7):
                campo = f"{atributo}_{clave_sel}"
                with columnas_atrib[i % 3]:
                    grafica(barra_pct(
                        df_res, campo,
                        ETIQUETAS_ATRIBUTO.get(atributo,
                                               atributo.replace("_", " ").title()),
                        altura=215))
            st.caption(
                "Los atributos se preguntan solo a quien conoce al aspirante; "
                "«Es buen(a) candidato(a)» además solo a quien dio opinión "
                "«Buena», así que su base es la más reducida de todas.")

            st.markdown("---")

            # ── Preferencia MORENA ────────────────────────────────────────────
            st.markdown(
                '<div class="sec-title">Preferencia de candidatura por MORENA</div>',
                unsafe_allow_html=True)
            grafica(barra_pct(df_res, "preferencia_total_morena",
                              "P19.1 Preferencia total", altura=330))

            with st.expander("Cruce de preferencia por sexo y rango de edad"):
                if "preferencia_total_morena" in df_res.columns:
                    cruce = pd.crosstab(
                        df_res["preferencia_total_morena"],
                        [df_res["sexo_cuota"], df_res["rango_edad_cuota"]],
                        normalize="columns", dropna=False).mul(100).round(1)
                    st.dataframe(cruce, use_container_width=True)
                    st.caption(
                        "Porcentajes por columna. Bases pequeñas por celda en "
                        "etapas tempranas del operativo — interpretar con cautela.")