"""
Atlixco PIE L3 · diagnostico_bubble_L3.py

Diagnóstico de SOLO LECTURA contra la Data API de Bubble. No escribe nada,
no depende de Streamlit. Correr desde la carpeta del proyecto:

    python diagnostico_bubble_L3.py

Lee la private key, en este orden:
    1. variable de entorno BUBBLE_PRIVATE_KEY
    2. .streamlit/secrets.toml → [bubble] private_key

Qué responde — cada punto es algo que config.py asume y que hay que confirmar
antes de que empiece el levantamiento:

  1. ¿Responde el Thing type "Encuesta" en el endpoint configurado?
  2. ¿Qué campos trae realmente un registro vs los 137 del FIELD_MAP?
     - en FIELD_MAP pero no en Bubble  → nombre mal escrito (o campo vacío)
     - en Bubble pero no en FIELD_MAP  → campo nuevo sin mapear
  3. ¿Cómo llega `seccion_electoral`? (string con cero a la izquierda, int…)
     ¿Y qué capturan en la sección 160 reseccionada?
  4. ¿Cuál es el valor exacto de `municipio_texto`?
  5. ¿Existe algún campo de estatus/completitud que no estemos usando?
  6. ¿Cómo llegan los campos de grabación (P19_1_grabacion y su duración)?
  7. ¿Cómo llegan latitud/longitud: float o string?
  8. Distribución de duracion_min y volumen por día — para calibrar umbrales
     y ver cuántas capturas de prueba/capacitación habría que descartar.
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BUBBLE_ENDPOINT, BUBBLE_PAGE_SIZE, FIELD_MAP, MODO,
    INICIO_OPERATIVO, FECHAS_EXCLUIDAS, METAS_POR_SECCION,
)

SEP = "─" * 72
TZ_MX = ZoneInfo("America/Mexico_City")

# Thing types a probar si el configurado falla.
THING_TYPES_ALTERNOS = ["Encuesta", "encuesta", "Survey", "survey", "Respuesta"]

# Palabras que sugieren un campo de estatus/completitud no mapeado.
PALABRAS_ESTATUS = [
    "status", "estatus", "terminad", "complet", "finaliz", "step", "paso",
    "progress", "avance", "submit", "enviad", "draft", "borrador",
]


# ── Credenciales ──────────────────────────────────────────────────────────────

def leer_private_key() -> str:
    key = os.getenv("BUBBLE_PRIVATE_KEY", "").strip()
    if key:
        print("→ Private key leída de BUBBLE_PRIVATE_KEY")
        return key

    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", "secrets.toml")
    if not os.path.exists(ruta):
        sys.exit(
            "❌ No hay private key.\n"
            '   export BUBBLE_PRIVATE_KEY="tu_key"\n'
            f"   o crea {ruta} con:\n"
            "       [bubble]\n"
            '       private_key = "tu_key"'
        )
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    with open(ruta, "rb") as f:
        secrets = tomllib.load(f)
    key = secrets.get("bubble", {}).get("private_key", "")
    if not key:
        sys.exit("❌ No se encontró [bubble] private_key en secrets.toml.")
    print(f"→ Private key leída de {ruta}")
    return key


# ── 1. Conectividad y Thing type ──────────────────────────────────────────────

def verificar_endpoint(headers: dict) -> str:
    print(f"\n{SEP}\n1. CONECTIVIDAD Y THING TYPE\n{SEP}")
    print(f"  MODO en config.py : {MODO}")
    print(f"  Endpoint          : {BUBBLE_ENDPOINT}")

    resp = requests.get(BUBBLE_ENDPOINT, headers=headers,
                        params={"limit": 1}, timeout=20)
    print(f"  → HTTP {resp.status_code}")
    if resp.status_code == 200:
        total = resp.json().get("response", {}).get("count", "?")
        print(f"  ✅ Thing type correcto · {total} registros en la tabla")
        return BUBBLE_ENDPOINT

    print("  ✗ El endpoint configurado no responde. Probando alternativas…")
    raiz = BUBBLE_ENDPOINT.rsplit("/", 1)[0]
    for thing in THING_TYPES_ALTERNOS:
        url = f"{raiz}/{thing}"
        r = requests.get(url, headers=headers, params={"limit": 1}, timeout=20)
        print(f"    {thing:12s} → HTTP {r.status_code}", end="")
        if r.status_code == 200:
            print("  ✅")
            print(f"\n  ⚠️  Corregir BUBBLE_ENDPOINT en config.py a: {url}")
            return url
        print()
    sys.exit("\n❌ Ningún Thing type respondió 200. Revisar la key y la URL base.")


# ── 2. Descarga completa ──────────────────────────────────────────────────────

def descargar_todo(endpoint: str, headers: dict) -> list[dict]:
    print(f"\n{SEP}\n2. DESCARGA DE REGISTROS\n{SEP}")
    registros: list[dict] = []
    cursor = 0
    while True:
        resp = requests.get(endpoint, headers=headers,
                            params={"limit": BUBBLE_PAGE_SIZE, "cursor": cursor},
                            timeout=30)
        resp.raise_for_status()
        cuerpo = resp.json().get("response", {})
        lote = cuerpo.get("results", [])
        registros.extend(lote)
        restantes = cuerpo.get("remaining", 0)
        print(f"  cursor {cursor:>5} → +{len(lote):>3} registros · faltan {restantes}")
        if restantes <= 0 or not lote:
            break
        cursor += BUBBLE_PAGE_SIZE
    print(f"\n  Total descargado: {len(registros)}")
    return registros


# ── 3. FIELD_MAP vs campos reales ─────────────────────────────────────────────

def comparar_field_map(registros: list[dict]) -> None:
    print(f"\n{SEP}\n3. FIELD_MAP vs CAMPOS REALES\n{SEP}")

    presentes: set[str] = set()
    for r in registros:
        presentes.update(r.keys())

    mapeados = set(FIELD_MAP.keys())
    sin_llegar = sorted(mapeados - presentes)
    sin_mapear = sorted(presentes - mapeados)

    print(f"  FIELD_MAP: {len(mapeados)} campos · Bubble devuelve: {len(presentes)}")

    print(f"\n  ── En FIELD_MAP pero NUNCA presentes ({len(sin_llegar)}) ──")
    print("     (Bubble omite campos sin valor: si nadie contestó esa pregunta")
    print("      todavía, esto es normal. Si YA hay respuestas, es mapeo malo.)")
    for k in sin_llegar:
        print(f"     ✗ {k}  → {FIELD_MAP[k]}")
    if not sin_llegar:
        print("     ✅ ninguno — los 137 campos llegan")

    print(f"\n  ── En Bubble pero SIN mapear ({len(sin_mapear)}) ──")
    for k in sin_mapear:
        marca = "  ⚠️ posible estatus" if any(p in k.lower() for p in PALABRAS_ESTATUS) else ""
        print(f"     + {k}{marca}")
    if not sin_mapear:
        print("     ✅ ninguno")

    print("\n  ── Cobertura de respuesta por campo mapeado ──")
    print("     (cuántos registros traen valor no vacío — 0 en un campo que")
    print("      debería estar contestado delata un nombre mal escrito)")
    n = len(registros)
    vacios = []
    for llave, destino in FIELD_MAP.items():
        con_valor = sum(1 for r in registros if r.get(llave) not in (None, "", [], {}))
        if con_valor == 0:
            vacios.append((llave, destino))
    for llave, destino in vacios:
        print(f"     0/{n}  {llave:28s} → {destino}")
    if not vacios:
        print(f"     ✅ los {len(FIELD_MAP)} campos tienen al menos una respuesta")


# ── 4. Campos críticos ────────────────────────────────────────────────────────

def revisar_campos_criticos(registros: list[dict]) -> None:
    print(f"\n{SEP}\n4. CAMPOS CRÍTICOS — TIPO Y VALORES REALES\n{SEP}")

    def muestra(llave: str, n: int = 6) -> list:
        vals = [r.get(llave) for r in registros if r.get(llave) not in (None, "", [])]
        return vals[:n]

    for llave in ("seccion_electoral", "municipio_texto", "latitud", "longitud",
                  "nombre_encuestador", "Created By"):
        vals = muestra(llave)
        tipos = {type(v).__name__ for v in vals}
        print(f"\n  {llave}")
        print(f"    tipos  : {tipos or '— sin datos —'}")
        print(f"    muestra: {vals}")

    # — Sección electoral: valores distintos y la 160 —
    secs = Counter(str(r.get("seccion_electoral")) for r in registros
                   if r.get("seccion_electoral") not in (None, ""))
    print(f"\n  ── Valores distintos de seccion_electoral ({len(secs)}) ──")
    for val, cuenta in sorted(secs.items()):
        en_muestra = ""
        try:
            if int(float(val)) in METAS_POR_SECCION:
                en_muestra = " ✅ en muestra"
            else:
                en_muestra = " ⚠️ FUERA de la muestra L3"
        except (ValueError, TypeError):
            en_muestra = " ⚠️ no numérico"
        print(f"     {val!r:>10}: {cuenta:>4}{en_muestra}")

    reseccion = [v for v in secs if "3119" in v or "3120" in v or "3121" in v
                 or "3122" in v or "3123" in v]
    print("\n  Sección 160 (reseccionada): se espera que capturen '160'.")
    if reseccion:
        print(f"     ⚠️  Se encontraron capturas con sección vigente nueva: {reseccion}")
        print("         → avisar a campo o mapear 3119-3123 → 160 en la transformación")
    else:
        print("     ✅ sin capturas con numeración 3119-3123")

    # — Grabación —
    print(f"\n  ── Campos de grabación (P19_1) ──")
    for llave in ("P19_1_grabacion", "P19_1_grabacion_duracion"):
        vals = muestra(llave, 4)
        con = sum(1 for r in registros if r.get(llave) not in (None, "", [], {}))
        print(f"    {llave}")
        print(f"      presentes: {con}/{len(registros)} · tipos: {{{', '.join({type(v).__name__ for v in vals})}}}")
        print(f"      muestra  : {vals}")

    # — Campos candidatos a estatus —
    presentes: set[str] = set()
    for r in registros:
        presentes.update(r.keys())
    candidatas = [k for k in presentes if any(p in k.lower() for p in PALABRAS_ESTATUS)]
    print(f"\n  ── Campos con pinta de estatus/completitud ──")
    if candidatas:
        for k in candidatas:
            vals = Counter(str(r.get(k)) for r in registros)
            print(f"    {k}: {dict(vals.most_common(6))}")
    else:
        print("    ninguno — se confirma que `terminada` hay que derivarla de la duración")


# ── 5. Fechas y duraciones ────────────────────────────────────────────────────

def revisar_fechas_y_duracion(registros: list[dict]) -> None:
    print(f"\n{SEP}\n5. FECHAS, DURACIÓN Y CORTE DEL OPERATIVO\n{SEP}")

    def a_local(valor):
        if not valor:
            return None
        try:
            return (datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
                    .astimezone(TZ_MX))
        except ValueError:
            return None

    por_dia = Counter()
    duraciones = []
    sin_modified = 0

    for r in registros:
        creado = a_local(r.get("Created Date"))
        modif = a_local(r.get("Modified Date"))
        if creado:
            por_dia[creado.date().isoformat()] += 1
        if not r.get("Modified Date"):
            sin_modified += 1
        if creado and modif:
            duraciones.append(round((modif - creado).total_seconds() / 60, 1))

    print("  ── Registros por día (hora local de México) ──")
    for dia in sorted(por_dia):
        marca = ""
        if dia in FECHAS_EXCLUIDAS:
            marca = "  ← EXCLUIDO (capacitación)"
        elif dia < INICIO_OPERATIVO:
            marca = "  ← previo al operativo, se descarta"
        print(f"     {dia}: {por_dia[dia]:>4}{marca}")

    validos = sum(n for d, n in por_dia.items()
                  if d >= INICIO_OPERATIVO and d not in FECHAS_EXCLUIDAS)
    print(f"\n  Total: {len(registros)} · dentro del operativo: {validos} "
          f"· descartados: {len(registros) - validos}")
    print(f"  Registros sin 'Modified Date': {sin_modified}")

    if duraciones:
        duraciones.sort()
        n = len(duraciones)

        def pct(p):
            return duraciones[min(int(n * p), n - 1)]

        print(f"\n  ── duracion_min = Modified − Created (n={n}) ──")
        print(f"     min {duraciones[0]} · p10 {pct(.10)} · p25 {pct(.25)} · "
              f"mediana {pct(.50)} · p75 {pct(.75)} · p90 {pct(.90)} · max {duraciones[-1]}")
        for umbral in (1, 2, 3, 5, 8):
            print(f"     < {umbral:>2} min: {sum(1 for d in duraciones if d < umbral):>4}")
        print(f"     > 40 min: {sum(1 for d in duraciones if d > 40):>4}")
        print("\n     Usar esto para calibrar DUR_MIN_MIN / DUR_MAX_MIN en config.py.")


# ── 6. Registro de muestra completo ───────────────────────────────────────────

def volcar_muestra(registros: list[dict]) -> None:
    print(f"\n{SEP}\n6. REGISTRO DE MUESTRA (JSON crudo del más reciente)\n{SEP}")
    ordenados = sorted(registros, key=lambda r: str(r.get("Created Date", "")), reverse=True)
    print(json.dumps(ordenados[0], indent=2, ensure_ascii=False)[:4000])

    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "muestra_cruda_bubble_L3.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(ordenados[:5], f, indent=2, ensure_ascii=False)
    print(f"\n  → 5 registros crudos guardados en {ruta}")


def main() -> None:
    key = leer_private_key()
    headers = {"Authorization": f"Bearer {key}"}

    endpoint = verificar_endpoint(headers)
    registros = descargar_todo(endpoint, headers)

    if not registros:
        print("\n⚠️  La tabla está vacía. Captura 2-3 registros de prueba y vuelve a correr:")
        print("    sin datos no se puede validar FIELD_MAP ni calibrar umbrales.")
        return

    comparar_field_map(registros)
    revisar_campos_criticos(registros)
    revisar_fechas_y_duracion(registros)
    volcar_muestra(registros)

    print(f"\n{SEP}")
    print("✅ Diagnóstico completo. Pega la salida completa de vuelta en el chat")
    print("   para cerrar FIELD_MAP y calibrar los umbrales de flags.")
    print(SEP)


if __name__ == "__main__":
    main()
