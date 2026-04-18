# Local zero-shot classifier — pin + upgrade notes

Plan Consolidado item **0.21** + **F.8**.

## Current pin

| field           | value                                                                     |
|-----------------|---------------------------------------------------------------------------|
| model id        | `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`                |
| revision (SHA)  | `b5113eb38ab63efdd7f280f8c144ea8b13f978ce`                                |
| disk footprint  | ~570 MB (safetensors + tokenizer + config)                               |
| backend         | CPU-only (torch)                                                          |
| wrapped by      | `src/classifier_local.py` (`LocalZeroShotClassifier`)                     |
| consumer        | `src/hooks/auto_capture.py::_zero_shot_classify` (wave-2 wiring)          |
| last pin date   | 2026-04-19 (wave-2 release)                                               |
| load status     | green under transformers ≥ 5.3.0 (previous `mDeBERTa-v3-base-mnli-xnli` pin failed `model_type` lookup and was dropped during wave-2). |

The model must be loaded with `revision=<SHA>` so every operator (Francisco,
Nora, and any future install) downloads the exact same weights. This
guarantees reproducibility of classifier decisions across machines and
makes FP/FN regression analysis meaningful.

## Upgrade policy

1. A monthly reminder fires via `nexo_reminder_create` with
   `recurrence=monthly`, description: *"Revisar upgrade del clasificador
   local del Guardian (Fase 0.21) — leer docs/classifier-model-notes.md y
   comparar con HF upstream"*.
2. **Never auto-upgrade.** When the reminder triggers:
   - Compare upstream revisions on HuggingFace Hub.
   - Evaluate on the fixtures in `tests/fixtures_rules_validation.json`.
   - If the new revision is strictly better (F1 on correction detection
     ≥ current F1 and no regression on neutral-sentence FP), bump the
     pin in this file and in `src/classifier_local.py`.
   - Ship as a patch release of NEXO Core with a CHANGELOG entry:
     `fix: classifier pin → <new-sha>`.
3. Alternative candidates (for the next eval cycle):
   - `BAAI/bge-m3` (multilingual, richer embeddings, ~1.2 GB)
   - `intfloat/multilingual-e5-base` (smaller, decent on romance
     languages)
   - `xlm-roberta-base` (lighter, needs task-specific fine-tuning)

## Why pin, not track `main`

`main` on HuggingFace can silently change tokenizer vocab, classifier
head weights, or breaking config names. A Guardian rule is only
reproducible if the classifier weights are deterministic. Pinning is
therefore a **requirement** of the Capa-2 contract, not an optimisation.
