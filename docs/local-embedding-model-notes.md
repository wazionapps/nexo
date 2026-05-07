# Local embedding + reranker models — pin and upgrade notes

This document closes the reproducibility gap for the FastEmbed-backed local
models used by Brain/Desktop retrieval.

Classifier status stays as-is and remains documented separately in
[docs/classifier-model-notes.md](/Users/franciscoc/Documents/_PhpstormProjects/nexo/docs/classifier-model-notes.md):

- `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`
- pinned by immutable HuggingFace revision SHA
- no auto-upgrade

## What was wrong

Before this change, the embedding + reranker stack loaded friendly model names
through `fastembed`:

- `BAAI/bge-base-en-v1.5`
- `BAAI/bge-small-en-v1.5`
- `Xenova/ms-marco-MiniLM-L-6-v2`

That was only **half-pinned**:

- NEXO code named a stable model id
- but the actual ONNX artifacts came from the current upstream repo head used by
  `fastembed`'s downloader
- so a future upstream rewrite could silently change weights/tokenizers without a
  NEXO release

That is not acceptable for reproducible retrieval/debugging across operators.

## Current pin

| name | runtime model id | pinned source repo | pinned revision |
|------|------------------|--------------------|-----------------|
| embeddings (base) | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` | `faf4aa4225822f3bc6376869cb1164e8e3feedd0` |
| embeddings (small / migration) | `BAAI/bge-small-en-v1.5` | `qdrant/bge-small-en-v1.5-onnx-q` | `52398278842ec682c6f32300af41344b1c0b0bb2` |
| reranker | `Xenova/ms-marco-MiniLM-L-6-v2` | `Xenova/ms-marco-MiniLM-L-6-v2` | `a09144355adeed5f58c8ed011d209bf8ee5a1fec` |

The base embedding model is intentionally multilingual and 384-dimensional.
That keeps the offline Desktop bundles below Windows installer limits while
fixing Spanish/French/German/Portuguese/Italian recall for new and updated
installs. Existing 768-dimensional stores are backed up and re-embedded because
the active model marker includes both the pinned model revision and dimension.

The full file manifest (required files + size + sha256) lives in:

- [src/local_model_manifest.json](/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/local_model_manifest.json)

Materialization / verification lives in:

- [src/local_models.py](/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/local_models.py)

## Runtime contract

1. Brain resolves the model through the in-repo manifest.
2. It downloads the pinned HF revision only.
3. It copies only the required files into `~/.nexo/runtime/models/...`.
4. It verifies size + sha256 for every required file.
5. FastEmbed loads from that exact directory via `specific_model_path`.
6. `cognitive.db` stores the active embedding model marker. On update, if the
   marker differs from the current manifest, Brain backs up the database and
   re-embeds STM, LTM, and quarantine rows with the pinned multilingual model.

This means the runtime does **not** trust `fastembed` to choose artifacts by
floating registry state.

## Upgrade policy

1. **Never auto-upgrade.**
2. A model upgrade is only valid when all of these change together in one repo release:
   - `src/local_model_manifest.json`
   - any loader/warmup code if needed
   - this document
   - tests covering the pin contract
3. Upgrade review must confirm:
   - source repo
   - immutable revision SHA
   - required file list
   - sha256 for every required file
   - smoke load on a clean machine via `nexo-brain warmup-models`
4. Release note should mention the exact revision bump.

## Clean-machine validation

On a clean machine:

1. install Brain normally
2. run `nexo-brain warmup-models --json`
3. verify the returned revisions match this document
4. confirm the managed runtime cache exists under `~/.nexo/runtime/models/`
5. run retrieval/reranker smoke paths

If the pinned files on disk do not match the manifest, warmup must fail rather
than silently accepting drift.
