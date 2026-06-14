# PLAN MAESTRO — Rediseño del Core de Memoria Local de NEXO

> Documento de arquitectura. Integra los 13 veredictos en una única hoja de ruta de rediseño + release. Toda afirmación sobre el código actual está citada `archivo:línea` por las dimensiones de origen; lo no verificado se marca como tal.

---

## 1. VISIÓN (estado objetivo)

El core de memoria local deja de ser un indexador léxico ciego y se convierte en **el sistema nervioso documental de Nero**: cuando Francisco dice *"trabajo con el proveedor X"*, el agente recibe — de forma determinista y en el mismo turno — el dossier vivo de esa entidad (facturas, IBAN, contactos, emails de pedidos/devoluciones, importes agregados y rango de fechas, todo con evidencia trazable a `local_asset:<id>#chunk:<id>`). El sistema recupera por **significado y por literal a la vez** (híbrido FTS5+vector con fusión RRF), no solo cuando la query casa textualmente; entrega el contexto **sin secretos, sin ruido CSS/HTML, y sin vaciarse en el truncado**; y lo hace **100% on-device, offline y empaquetable** en el bundle Electron, sin GPU. El objetivo supremo se cumple cuando el dossier de un proveedor **nunca llega vacío**, los importes/fechas **siempre** están presentes, y un gate de release **mide objetivamente** que no se degrada.

---

## 2. DIAGNÓSTICO CONSOLIDADO

**Salud global: FRÁGIL.** El sistema tiene buena arquitectura defensiva (privacidad en dos planos, fail-open, WAL, caps anti-blow-up) pero **tres clases de fallo silencioso** rompen el objetivo supremo de punta a punta. Agrupadas, no por dimensión:

### G-A. El dossier llega VACÍO al LLM (el fallo más grave y más barato de arreglar)
`_truncate_context_payload` (api.py:4562) solo recorta `chunks/assets/entities/relations` y **nunca** `facts/aggregates`. Con el default `max_facts=3000` (api.py:57), el payload de un proveedor real desborda `max_chars=20000` y cae a `_minimal_truncated_context_payload` (api.py:4520), que devuelve **todas las listas vacías y sin clave `aggregates`**. El oro del caso (importes vía `numeric_by_predicate`, fechas vía `date_range`, ya calculado en api.py:5120) **se tira**. Convergen aquí 5 dimensiones (KG, NER, Retrieval, API, Evaluación). **Sin este fix, ninguna otra mejora llega al agente.**

### G-B. No hay recall semántico real: el embedding está subordinado al léxico
El vector solo se evalúa sobre candidatos que ya pasaron un prefiltro `LIKE %term%` (api.py:4343-4356), así que paráfrasis/sinónimos/ES↔EN **no se recuperan**. Encima la fusión es `max()` (api.py:4843) que hace competir escalas dispares, el `vector_score` de la Capa A es **dot-product sin normalizar** (embeddings.py:137 — correcto hoy solo porque fastembed L2-normaliza al escribir, frágil ante swap de modelo), no hay FTS5/BM25 en `local_chunks`, y el reranker está **muerto en silencio** y además es inglés (ms-marco) sobre corpus español.

### G-C. La inyección al loop es discrecional del LLM, no determinista
El motor real (`pre_answer_router.py`) está bien construido (fail-open, tiers, deadlines) pero **nadie lo invoca por turno**: depende de que el LLM llame la tool (server.py:1689); R37 quedó en shadow (CHANGELOG.md:14). Además el índice de 19 GB **ni siquiera está en `allowed_sources` del tier standard** (pre_answer_runtime.py:207-222) y **no hay trigger por entidad nombrada**. Resultado directo: *"trabajo con el proveedor X"* no dispara las facturas en una pregunta normal.

### G-D. Ruido mecánico contamina entidades y facts
`clean_text` (extractors.py:274-278) borra tags HTML pero **no el contenido** de `<style>/<script>`, y colapsa todos los `\r\n` a un espacio. El CSS sobrevive y `_FIELD_SPAN_RE` lo convierte en facts; el regex de mayúsculas (extractors.py:369) captura `'Hola Juan'`, `'EUR Ver'` como entidades. **Es ruido mecánico, no un problema de IA** — se mata con regex a coste cero.

### G-E. Bug latente de empaquetado: parsers no declarados
`pypdf/openpyxl/extract_msg/numbers_parser` **no están en `src/requirements.txt`** (29 líneas); funcionan en la Mac de Francisco solo por el homebrew global. En un bundle Electron/WSL limpio se importan, fallan el `try/except` y devuelven `''` → **todos los PDF/XLSX/MSG se indexarían vacíos en producción sin error**. Además 9 tipos de alto valor (.doc/.xls/.ppt/.rtf/.odt/...) están marcados `extract` sin parser.

### G-F. El grafo del usuario no existe a nivel de datos
`local_relations` solo tiene 2 aristas, ambas `asset→X` (api.py:3014, 3355); **cero aristas entidad↔entidad**. Los 3 grafos cognitivos (knowledge/causal/claim) viven en `cognitive.db` y **solo ingieren datos internos de NEXO**, nunca los del usuario (grep en `src/local_context/` = 0). La canonicalización de organizaciones está rota (patrón persona `name:{apellido}:{inicial}` aplicado a ORG, extractors.py:348). *"Sé todo del proveedor X"* es estructuralmente imposible hoy.

### G-G. Contención SQLite y escala
Read-conn con `busy_timeout=1200ms` vs 15000ms de escritura (api.py:250 vs db.py:79) → causa directa del `database is locked`. El retry hace `close_local_context_db()` sobre el handle **cacheado** (api.py:276), invalidando `_CONN` para todos. Vectores como `vector_json TEXT` (~6-9KB/fila vs ~1.5KB BLOB) inflan los 19 GB. **Cero mantenimiento**: ningún VACUUM/checkpoint/optimize jamás. El techo de 60 GB solo pausa, no recupera. `memory_search` escribe en la ruta de lectura (memory_retrieval.py:204).

### G-H. No se puede MEDIR nada
Cero métricas IR en el repo (recall@/MRR/nDCG = 0). El gate de release no puntúa retrieval (release-readiness.yml:10-12). `f1_score` se importa desde `/tmp` (viola offline). El bug de G-A no tiene **ni un test** (los existentes usan `max_chars=50000`, nunca disparan la truncación).

---

## 3. DECISIONES TRANSVERSALES

Estas decisiones gobiernan varias dimensiones a la vez. Para cada una: opción elegida + por qué, contrastada.

### D1 — Motor vectorial: **BLOB float32 + brute-force vectorizado con numpy. NO sqlite-vec ahora.**
**Elegido:** migrar `vector_json TEXT → vector_blob BLOB float32` (patrón ya presente en Capa B, _core.py:1303), leer con `numpy.frombuffer`, scoring por matmul sobre el pool pre-filtrado por privacidad. Cero dependencias nuevas.
**Por qué:** el problema de recall **no es la escala** (es LIKE-gated, no brute-force global), así que ANN sublineal no se necesita aún. `sqlite-vec` es **código nativo nuevo por plataforma** (hoy no hay `load_extension` en el repo — verificado), pre-v1 y brute-force igualmente; añadiría riesgo de empaquetado cross-platform (WSL incluido) y un invariante de privacidad que debería ir **dentro** del KNN, sin dar HNSW real. **Descartado** `sqlite-vec/usearch/faiss` hasta tener profiling que demuestre que el brute-force numpy dejó de escalar (y entonces se reusa `hnsw_index.py` existente).

### D2 — Recuperación: **híbrido FTS5(bm25) + rama vectorial independiente → fusión RRF (k=60) + MMR.**
**Elegido:** FTS5 sobre `local_chunks` como rama léxica que **sustituye** Jaccard+LIKE; rama vectorial **independiente** (top-N por coseno sin gate léxico); unir ambas y fusionar por **Reciprocal Rank Fusion**; MMR (λ≈0.7) sobre el head para diversidad. El patrón FTS5+BM25+RRF **ya existe y está probado** en `cognitive` (_core.py:1124, _search.py:191,228).
**Por qué:** ataca las dos grietas críticas de retrieval (recall vectorial puro + fusión robusta a escalas dispares) con **cero dependencias binarias** — FTS5 es nativo de SQLite. RRF evita calibrar pesos sin ground-truth. **Pre-requisito oculto crítico:** `privacy_class` vive solo en `local_assets`, NO en `local_chunks` (_schema.py:1947 vs 1968) y los triggers FTS no pueden hacer JOIN → **hay que denormalizar `privacy_class` a `local_chunks` o re-filtrar post-MATCH**. **Y:** el `max()` esconde un guardrail intencional (api.py:4849-4852, evidencia directa > chunks no relacionados) que un RRF naive rompe — preservarlo como multiplicador post-RRF. **Descartado** combinación lineal calibrada (frágil sin ground-truth).

### D3 — Modelo de embeddings: **migrar a multilingual-e5-small (384d), pero SOLO en v1 y con A/B empírico en ES.**
**Elegido:** `intfloat/multilingual-e5-small` ONNX-Q (mantiene **384d** → no toca esquema ni rerank coseno; ~110-130MB vs 235MB actual; ventana **512** vs 128 del MiniLM actual). Requiere prefijos asimétricos `query:`/`passage:` en `embed_record` (embeddings.py:104) y registro ONNX custom con MEAN pooling + normalize.
**Por qué:** cierra la grieta crítica de **truncado silencioso del vector** (chunks de 900 chars contra ventana de 128 tokens del MiniLM actual). Misma dimensión = migración por la cola de refresh existente sin romper el store. **NO es "cero código"** como se sugirió: los prefijos E5 son obligatorios o degrada en silencio. **Condicionado a A/B real en ES** (los benchmarks MTEB/MMTEB son agregados multilingües, no específicos ES). **Descartado** bge-m3/Qwen3-Embedding/EmbeddingGemma (1024d/768d rompen esquema, x2.7 sobre 19 GB, licencia Gemma con avisos en el .exe).

### D4 — Estrategia de extracción/NER: **saneamiento mecánico por regex primero (quick-wins), modelo SOTA solo tras piloto ES.**
**Elegido:** el ruido es **mecánico**, no de IA → matarlo con (1) strip de `<style>/<script>` en `clean_text`, (2) stopwords/gazetteer negativo **dentro de la indexación** (hoy solo en query-time, api.py:4216), (3) vocabulario controlado de ~30-50 predicados canónicos mapeados por **el embedder ya bundleado**, (4) `entity_canonical` con normalización legal + Jaro-Winkler + coseno. **GLiNER2/modelo NER tipado solo en v2, condicionado a:** validar int8 no-vacío, decidir destino del Qwen muerto, aplicarlo solo a docs de alto valor.
**Por qué:** las 4 quick-wins cierran ~80% del ruido a coste S sin modelos nuevos. **Hecho clave corregido:** el Qwen3-0.6B (429MB) **nunca se ejecuta** — no hay binding llama.cpp en `src/` (verificado); es peso muerto. Quitarlo **financia casi por completo** un GLiNER2 int8 si se decide. **Descartado** Tika/extractous (JVM/Rust grande, rompen los caps anti-cartesiano), spaCy/Stanza (no extraen IBAN/NIF/importe), GLiNER2 "a ciegas" sin piloto ES.

### D5 — Unificación de memoria↔archivos: **router cross-store en proceso con RRF. NO ATTACH, NO índice monolítico.**
**Elegido:** módulo `unified_retrieval` que llama por separado a `memory_search()` y `context_query()` (ambos readonly) y fusiona por RRF. **Premisa corregida:** el lado memoria es **léxico puro** (sin vectores persistidos — memory_retrieval.py:27, sin columna embedding); RRF salva el plan porque es agnóstico a la naturaleza del score y tolera fusionar rangos léxicos (memoria) con rangos semánticos (archivos). Dar vectores al lado memoria es **v2 condicionado** a medir recall ES real.
**Por qué:** `context_query` ya expone entrypoint readonly limpio; el router aísla conexiones y evita lock cross-attach sobre la DB de 19 GB. **Descartado** ATTACH como primera opción (riesgo lock WAL+19GB), índice vectorial monolítico (XL, rompe el aislamiento anti-corrupción).

### D6 — Framework de evaluación: **métricas IR propias (~150 líneas numpy) + golden set sintético "proveedor X". NO ranx.**
**Elegido:** golden determinista de 50-100 casos (fixture JSON sintético redactado, pasa por privacy.py) con qrels + tripletas predicado-valor + lista de ruido NER; gate PR-blocking determinista que computa recall@k/MRR/nDCG/fact-accuracy/noise_rate **sin modelos** (corre en CI tal cual); capa nocturna semántica con el embedding real bajo el contrato de release existente (`max_age_hours:72`).
**Por qué:** **ranx arrastra Numba+llvmlite** (~30-40MB + toolchain) y el bundle hoy no trae numba/scipy (requirements = 29 líneas) — fricción cross-platform justo donde el invariante de empaquetado la prohíbe. Para 50-100 queries, las métricas son ~150 líneas triviales sobre el numpy ya presente. **Descartado** ranx/pytrec_eval (compilan C), LLM-juez Qwen3-0.6B (varianza/sesgo catastrófico), benchmarks externos CRAG/RAGBench/LoCoMo (miden la capa cognitive, no local_context).

### D7 — Concurrencia SQLite: **serializar escrituras del sidecar + quitar el close destructivo + paridad de busy_timeout.**
**Elegido:** replicar `_SerializedConnection/_write_lock` de _core.py:58 en el sidecar; **eliminar** el `close_local_context_db()` del retry (api.py:276); subir read `busy_timeout` 1200→15000ms (api.py:250); sacar el procesado de cola de la ruta de lectura (`process_queue=False`, memory_retrieval.py:200) a un worker.
**Por qué:** raíz operativa verificada del `database is locked`, con patrón ya probado en el mismo repo, riesgo bajo. **Descartado** daemon indexador separado (añade ciclo de vida; NEXO Desktop ya arrastra deuda de app-exit).

---

## 4. ARQUITECTURA OBJETIVO (pipeline de punta a punta)

Marcado **[CAMBIA]** lo que se rediseña, **[NUEVO]** lo que se añade, **[=]** lo que se preserva.

```
┌─ INGESTA ──────────────────────────────────────────────────────────────────┐
│ Cron 60s → run_once (scan + reconcile + jobs)                          [=]   │
│ [NUEVO] Watcher selectivo (watchdog) sobre roots de alto valor              │
│         → dirty-queue con prioridad sobre el round-robin O(N)                │
│ [CAMBIA] SF_DATALESS detect (pure-stdlib) → fase metadata-only para iCloud   │
│ [CAMBIA] EDEADLK clasificado como 'offloaded', no error de fiabilidad        │
│ [=] Gates privacidad: should_skip_tree/file + privacy_class + contains_secret│
│     ANTES de leer (innegociable)                                            │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ EXTRACCIÓN ───────────────────────────────────────────────────────────────┐
│ [CAMBIA] clean_text: strip <style>/<script> ANTES de tags; preservar \n      │
│ [NUEVO] Declarar pypdf/openpyxl/extract_msg/striprtf/olefile/numbers_parser  │
│         en requirements.txt (bug latente de empaquetado)                     │
│ [NUEVO] Parsers puros para .rtf/.odt/.doc/.numbers; flag needs_ocr           │
│ [=] Caps: MAX_TEXT_BYTES=512KB, PDF 50pp, xlsx 5 hojas; gate secretos        │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ CHUNKING ─────────────────────────────────────────────────────────────────┐
│ [CAMBIA] chunk_text recursivo (jerárquico) + router por metadata['extractor']│
│          email→separa cita/firma; CSV/XLSX→1 fila+header; prosa→recursivo    │
│ [CAMBIA] chunk_id estable: stable_id('chunk', f'{version_id}:{index}')       │
│          (elimina colisión PK que aborta el asset entero, api.py:2876)       │
│ [CAMBIA] target en tokens (ventana 512 de e5), cap 80→200 + parent-retrieval │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ NER / FACTS / GRAFO ──────────────────────────────────────────────────────┐
│ [CAMBIA] Stopwords+gazetteer negativo DENTRO de la indexación               │
│ [CAMBIA] Vocabulario canónico de predicados (importe/total/amount→importe)   │
│          mapeado por coseno con el embedder bundleado                        │
│ [NUEVO] entity_canonical: normaliz. legal + Jaro-Winkler + coseno (ORG)      │
│ [NUEVO] Aristas entidad↔entidad por co-ocurrencia + tipado regla            │
│          (IBAN→banco, NIF→empresa, patrón factura→proveedor)                 │
│ [v2] GLiNER2 int8 NER tipado SOLO en docs alto valor, tras piloto ES        │
│ [=] Caps anti-cartesiano: ENTITY_FACTS_MAX_PER_ASSET=200 (incidente 337M)   │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ EMBEDDINGS / ÍNDICE ──────────────────────────────────────────────────────┐
│ [CAMBIA] vector_json TEXT → vector_blob BLOB float32 (numpy.frombuffer)      │
│ [CAMBIA] embedder MiniLM-384d → e5-small-384d (ventana 512, prefijos q/p)    │
│ [NUEVO] FTS5(bm25) sobre local_chunks + triggers sync (privacy_class         │
│         denormalizada a local_chunks — pre-requisito)                        │
│ [CAMBIA] cosine normalizado defensivo; pin fastembed+onnxruntime (==)        │
│ [NUEVO] PRAGMAs: wal_autocheckpoint, mmap_size, cache_size; checkpointer     │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ RETRIEVAL / RERANK ───────────────────────────────────────────────────────┐
│ [CAMBIA] Rama léxica BM25 + rama vectorial INDEPENDIENTE (sin gate LIKE)     │
│ [CAMBIA] Fusión max() → RRF(k=60), preservando guardrail evidencia directa   │
│ [NUEVO] MMR(λ≈0.7) sobre el head antes de top-k                             │
│ [CAMBIA] Reranker: deja de morir en silencio (quitar except/lru_cache fail)  │
│ [v2] Reranker EN ms-marco → multilingüe bge-reranker-v2-m3 ONNX-CPU          │
│ [NUEVO] confidence real derivada de la fusión (no constante 0.75)            │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ API / DOSSIER ────────────────────────────────────────────────────────────┐
│ [CAMBIA] ★ _truncate_context_payload shape-aware por prioridad:             │
│          entity+recall+synthesis_contract+aggregates INTOCABLES →            │
│          top-N facts (rollup) → top-M chunks → candidates. NO _minimal.      │
│ [CAMBIA] max_facts default 3000→~120; rollup de facts por predicado+3 evid.  │
│ [CAMBIA] recall.assets_total = COUNT real pre-cap (no len(assets))           │
│ [CAMBIA] disambiguación blanda (top + low_confidence, no vacío)              │
│ [CAMBIA] get_asset: aplicar redact_path/is_queryable_path/contains_secret    │
│ [CAMBIA] EGRESO de search/relations re-verifica contains_secret (fuga real)  │
└──────────────────────────────────────────────────────────────────────────────┘
        │
┌─ INYECCIÓN AL LOOP ────────────────────────────────────────────────────────┐
│ [NUEVO] ★ Hook determinista por turno → pre_answer_route (como heartbeat)    │
│ [CAMBIA] local_context en allowed_sources de tier standard + PRIMARY en      │
│          prior_work/memory_question                                          │
│ [NUEVO] Trigger por entidad nombrada (reusa mDeBERTa-XNLI ya bundleado)      │
│         → fuerza local_context primary + tier deep                           │
│ [CAMBIA] corte 0.72→general: degradar conservador, no apagar la fabric       │
│ [NUEVO] router cross-store memoria↔archivos con RRF                         │
│ [CAMBIA] gate de gap: declarar 'no verificado' cuando hay entidad conocida   │
│          y no se trajo nada                                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. PLAN DE RELEASE POR FASES

### RELEASE A — Quick-wins (esta semana). Riesgo bajo, sin modelos nuevos, corre en CI tal cual.

| # | Dim | Qué se hace | Archivos previsibles | Esf | Imp | Riesgo | Dep |
|---|-----|-------------|---------------------|-----|-----|--------|-----|
| A1 ★ | API | **Fix entity_dossier vacío:** truncador recorta facts/aggregates por confidence ANTES de `_minimal`; aggregates intocables; `max_facts` 3000→~120 + top-N facts | `local_context/api.py` (4562, 5332), `server.py` (1438) | S | **Transformador** | bajo | — |
| A2 ★ | API/Store | **Matar `database is locked`:** read `busy_timeout` 1200→15000; quitar `close_local_context_db()` del retry; `process_queue=False` en memory_search → worker | `local_context/api.py` (250,276), `memory_retrieval.py` (200) | S | alto | bajo | — |
| A3 | Extracción/NER | **Strip CSS/JS** en clean_text + stopwords/gazetteer negativo en indexación | `local_context/extractors.py` (274), `api.py` (3075,369) | S | alto | bajo | — |
| A4 ★ | Extracción | **Declarar parsers** en requirements.txt (pypdf/openpyxl/extract_msg/...) + cablear numbers_parser ya instalado | `src/requirements.txt`, `extractors.py` | S | alto | bajo | empaquetado (verificar wheels cp) |
| A5 | Privacidad | **Cerrar fuga de egreso:** contains_secret en search (api.py:4874) y relations (4893), como ya hace el dossier | `local_context/api.py` (4874,4893) | S | alto | bajo | — |
| A6 | Privacidad | SENSITIVE_NAME_MARKERS de substring→token preciso (recupera facturas falsamente bloqueadas) | `privacy.py` (392,43-44) | S | medio | bajo | — |
| A7 | Embeddings | cosine normalizado defensivo + **pin fastembed/onnxruntime (==)** + PRAGMAs mmap/cache/wal_autocheckpoint | `embeddings.py` (134), `db.py` (75-100), `requirements.txt` | S | medio | bajo | — |
| A8 | Chunking | chunk_id estable `{version_id}:{index}` (elimina colisión PK que aborta el asset) | `local_context/api.py` (2873) | S | medio | bajo | — |
| A9 | Ingesta | SF_DATALESS detect + EDEADLK clasificado (desbloquea iCloud, pure-stdlib) + quitar cap de 20 errores + métrica backlog_drain_rate | `local_context/api.py` (663,2294,2486), `privacy.py` (437) | S | alto | bajo | — |
| A10 ★ | Evaluación | **Test rojo del path de truncación** a `max_chars=20000` (cierra A1 y lo vuelve detectable) + vendorizar f1_score fuera de /tmp | `tests/test_local_context_entity_dossier.py`, `benchmarks/metrics.py` | S | alto | bajo | A1 |

**Resultado de Release A:** el dossier deja de llegar vacío, desaparece el `database is locked`, se va el ruido CSS, se cierra el bug de empaquetado y la fuga de secretos, y existe el primer test que protege todo esto. **Es el mayor salto del proyecto y el de menor riesgo.**

---

### RELEASE B — v1 estructural. Cambios de esquema + modelo, requieren re-index/re-embed coordinado Brain+Desktop.

| # | Dim | Qué se hace | Archivos previsibles | Esf | Imp | Riesgo | Dep |
|---|-----|-------------|---------------------|-----|-----|--------|-----|
| B1 ★ | API | **Truncador shape-aware** por prioridad de sección + **rollup de facts** (por predicado, agregado + 3 evidencias) | `local_context/api.py` (4562,5266) | M | Transformador | medio | A1 |
| B2 ★ | Store/Retrieval | **vector_json → BLOB float32** + scoring batch numpy | `_schema.py` (2009), `api.py` (4829), `embeddings.py` | M | alto | medio | contrato profile model_id+rev+dim |
| B3 ★ | Retrieval/Chunking | **FTS5(bm25) sobre local_chunks + RRF + MMR**, sustituyendo LIKE/Jaccard/max() | `_schema.py` (triggers), `api.py` (4343,4843), `extractors.py` | M-L | Transformador | medio | **denormalizar privacy_class a local_chunks (pre-req)**; B2; A1 |
| B4 ★ | Embeddings/Chunking | **Migrar a e5-small-384d** (prefijos query/passage, registro ONNX) + chunk recursivo type-aware + cap 80→200 | `local_model_manifest.json`, `embeddings.py` (104), `extractors.py` (393), `migrate_embeddings.py` | M | Transformador | medio | re-embed 19GB; A/B ES; B2 |
| B5 | NER | **Vocabulario canónico de predicados** por coseno (de-fragmenta importe/total/amount) | `local_context/api.py` (3030) | M | alto | medio | B4 (si cambia embedder, recomputar vectores de predicados) |
| B6 | KG | **Aristas entidad↔entidad por co-ocurrencia + tipado regla** + índice target_ref + poblar entity_live_profile.relations | `local_context/api.py` (3014), `_schema.py` (2055), `entity_live_profile.py` (549) | S-M | alto | bajo | A1 (sin truncador, no llega al LLM) |
| B7 ★ | Inyección | **Hook determinista por turno → pre_answer_route** + local_context en tier standard + arreglar corte 0.72→general | cliente Desktop/CLI (hook), `pre_answer_router.py` (866), `pre_answer_runtime.py` (207) | M | Transformador | medio | A2 (lecturas extra → vigilar contención); paridad Brain↔Desktop |
| B8 | Memoria↔índice | **Router cross-store RRF** (memory_search + context_query) + hook diary→observations | `memory_retrieval.py`, nuevo `unified_retrieval.py` | M | Transformador | medio | A2; A1 |
| B9 | Privacidad | Ampliar SECRET_PATTERNS (Stripe/Twilio/SendGrid/GCP SA/connstrings) + entropía acotada + PII estructurado ES en redact_path | `extractors.py` (39), `util.py` (56) | M | alto | medio | validar contra corpus ES (FP sobre IDs de factura) |
| B10 ★ | Evaluación | **Golden set "proveedor X"** (50-100 casos, qrels+tripletas+ruido) + **gate PR-blocking** con métricas IR propias (~150 líneas) | `tests/test_retrieval_quality_gate.py`, `benchmarks/golden/`, `benchmarks/metrics.py` | M | Transformador | medio | release-contracts |
| B11 | Inyección | Decidir destino del **Qwen3-0.6B muerto** (429MB): quitar del manifest o cablear inferencia real | `local_model_manifest.json`, `model_warmup.py` | S | medio | bajo | decisión producto |

**Riesgos de B:** todo lo que toque formato de vector/modelo invalida embeddings de 19 GB → migración por `migrate_embeddings.py` + `EMBEDDING_REFRESH_JOB`, **nunca hot-edit**. El re-embed debe hacerse **una sola vez** (coordinar B4 con B2/B3). **B3 bloqueado por el pre-requisito de privacy_class** — resolverlo primero.

---

### RELEASE C — v2 / futuro. Tras profiling y pilotos que justifiquen el coste.

| # | Dim | Qué se hace | Esf | Condición de entrada |
|---|-----|-------------|-----|----------------------|
| C1 | NER/KG | **GLiNER2 int8** NER tipado + RE, SOLO docs alto valor | XL | piloto ES no-vacío + int8 validado + Qwen retirado (B11) financia bundle |
| C2 | Retrieval | **Reranker multilingüe** bge-reranker-v2-m3 ONNX-CPU (reemplaza ms-marco EN) | L | tras B3; release coordinada con bundle |
| C3 | Chunking | **Contextual Retrieval** on-device con Qwen3-0.6B (si se cablea inferencia) | L | validar latencia/calidad ES a escala 265k |
| C4 | Memoria | **Vectores al lado memoria** (mismo modelo, embebido en worker) | M | medir que RRF léxico se quedó corto en ES |
| C5 | Store | Mantenimiento+eviction en Deep Sleep (incremental_vacuum, purga por antigüedad/uso, GC ruido NER) | M | — |
| C6 | KG | NLI multilingüe (mDeBERTa) para contradicciones sobre pares pre-filtrados por HNSW | M | claim_graph conectado al pipeline |
| C7 | Ingesta | OCR diferido (Apple Vision macOS / RapidOCR ONNX WSL) para PDFs escaneados | L | flag needs_ocr (en B) + cola de baja prioridad |
| C8 | Extracción | Watcher de eventos (watchdog) selectivo → dirty-queue sub-minuto | L | drenar backlog primero; resolver gap WSL /mnt/c |
| C9 | Evaluación | Capa nocturna semántica + gate retrieval_quality en contrato de release | L | golden (B10) |
| C10 | Store | ProcessPoolExecutor para extracción CPU-bound | L | profiling que confirme cuello CPU tras B |

---

## 6. MÉTRICAS DE ÉXITO / EVALUACIÓN

**Golden set determinista "proveedor X"** (B10): 50-100 casos sintéticos redactados (pasan por privacy.py) de un proveedor con facturas/contactos/emails/importes/fechas coherentes, con `qrels` de relevancia + tripletas predicado-valor esperadas + lista de tokens-ruido (CSS/HTML) que NO deben aparecer.

**Métricas (todas con implementación propia ~150 líneas numpy, deterministas, en CI sin modelos):**
- **recall@k / MRR / nDCG@k** — ¿el documento relevante entra y rankea alto?
- **fact-accuracy** (precisión/recall de tripletas) — ¿el importe/fecha/contacto es el correcto? Mide directamente *"no se equivoca"*.
- **noise_rate** — % de facts/entidades que son ruido CSS/HTML. Debe tender a 0.
- **dossier_non_empty_rate** — % de dossiers de entidad pesada que **no** caen a `_minimal`. **Debe ser 100%** (es el KPI de G-A).
- **assets_total honesto** — el LLM sabe si vio 10 de 10 o 10 de 4000.

**Gate de release (2 niveles):**
1. **PR-blocking determinista** (`test_retrieval_quality_gate.py`): corre con caps de producción (`max_chars=20000`), sin modelos, asevera umbrales mínimos en cada métrica. Bloquea el merge si hay regresión.
2. **Nocturno semántico** (v2): cron local con el embedding real → run-file → nuevo gate `retrieval_quality` en `release-contracts/vX.json` con `evidence_required` + `max_age_hours:72` (schema existente, no toca `verify_release_readiness.py`).

**Test centinela inmediato (A10):** reproduce el path del bug de truncación a `max_chars=20000` y asevera que facts/aggregates sobreviven. Es el primer guardián y la semilla del golden.

---

## 7. RIESGOS Y MITIGACIÓN

| Riesgo | Severidad | Mitigación |
|--------|-----------|------------|
| **Re-embed de 19 GB doble** (si B2/B3/B4 se hacen en releases distintas) | alto | Agrupar B2+B3+B4 en una sola ventana de re-embed; migración por `migrate_embeddings.py`, nunca hot-edit |
| **e5-small no supera a MiniLM en ES** (benchmarks son agregados) | medio | A/B empírico con queries reales del usuario ANTES de comprometer el re-embed; misma dim permite rollback barato |
| **RRF naive rompe el guardrail de evidencia directa** (api.py:4849) | medio | Preservar el guardrail como multiplicador post-RRF, no como criterio fusionado; cubrir con test de query de entidad |
| **privacy_class no está en local_chunks** → fuga vía FTS5 | **crítico** | Pre-requisito DURO de B3: denormalizar privacy_class a local_chunks o re-filtrar post-MATCH. No mergear FTS5 sin esto |
| **detect-secrets/entropía → falsos positivos sobre IDs de factura ES** | medio | NO adoptar motor de entropía sin tests sobre corpus ES real; empezar solo con regex de proveedor |
| **Hook determinista agrava contención SQLite** (lecturas extra/turno) | medio | Hacer B7 después de A2; cache TTL ya existente; tiers acotan (small-talk→instant) |
| **GLiNER2 casi duplica el bundle** (~840MB actual) | alto (v2) | Validar int8 no-vacío + retirar Qwen muerto (B11) que financia el espacio; solo docs alto valor |
| **WSL no escanea /mnt/c** (roots=[home], api.py:649) | medio | Gap previo a watcher (C8); destapa que toda la ingesta Windows está confinada a WSL — abordar antes que el watcher |
| **Paridad Mac/Windows** (regla durable) | medio | Todo cambio de chunking/modelo/hook mantiene paridad funcional; release coordinada |

---

## 8. PRIMER PASO RECOMENDADO

**Empezar HOY por A1 + A10 juntos**, en este orden:

1. **A10 primero (test rojo):** escribir en `tests/test_local_context_entity_dossier.py` un caso que construya una entidad pesada (cientos de facts) y llame `entity_dossier` con `max_chars=20000` (cap real de producción, no el 50000 que usan los tests actuales), aseverando que el payload **NO** cae a `_minimal_truncated_context_payload` y que `facts`/`aggregates` sobreviven. **Hoy ese test falla** — eso confirma el bug y lo vuelve detectable.
2. **A1 (el fix):** extender `_truncate_context_payload` (api.py:4562) para recortar `facts`/`aggregates` por confidence ANTES de caer a `_minimal`, dejar `aggregates` intocables, y bajar `max_facts` default 3000→~120 (server.py:1438) con top-N de facts previo al truncado. El test de A10 pasa a verde.

**Por qué este primer paso:** es el único cambio que, por sí solo, convierte *"Nero no sabe nada del proveedor"* en *"Nero ve totales + fechas + top facturas"*, en horas, sin modelos ni esquema, con riesgo casi nulo. Es la grieta G-A, raíz de la que dependen 5 dimensiones, y deja el guardián que protege todo lo que viene después. **Ninguna otra mejora del plan llega al agente mientras el dossier siga vaciándose en el truncado.**

---

He integrado los 13 veredictos en este único plan maestro. Los puntos load-bearing que conviene que el lector retenga: (1) el fix de `_truncate_context_payload` (api.py:4562) es el primer paso absoluto — sin él nada llega al LLM; (2) las decisiones transversales clave son BLOB+numpy en lugar de sqlite-vec, FTS5+RRF nativo, e5-small-384d con A/B ES, saneamiento regex antes que GLiNER2, y métricas IR propias en lugar de ranx; (3) el pre-requisito oculto que puede descarrilar B3 es que `privacy_class` no existe en `local_chunks`; (4) el Qwen3-0.6B de 429MB es peso muerto verificado que financia un futuro GLiNER2 int8.