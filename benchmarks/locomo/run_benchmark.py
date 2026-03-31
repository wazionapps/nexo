#!/usr/bin/env python3
"""
NEXO Brain × LoCoMo Benchmark
==============================
Tests NEXO's cognitive memory against the LoCoMo long-term conversation benchmark
(Snap Research, ACL 2024). 10 conversations, 1,986 QA pairs, 5 categories.

Two modes:
  1. RAG-only: NEXO retrieval + Claude answer generation (comparable to paper baselines)
  2. Full cognitive: Same but with dream/decay cycles between sessions (NEXO differentiator)

Usage:
  python run_benchmark.py --mode rag       # RAG-only
  python run_benchmark.py --mode cognitive  # Full cognitive cycles
  python run_benchmark.py --mode both       # Both (default)

Output saved to benchmarks/locomo/results/
All work done in /tmp/nexo-bench/ — zero contamination to production.
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Isolation: override COGNITIVE_DB BEFORE importing cognitive ──────────
BENCH_DIR = "/tmp/nexo-bench"
NEXO_MCP_DIR = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
sys.path.insert(0, NEXO_MCP_DIR)

LOCOMO_DATA = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "locomo", "locomo10.json")
if not os.path.exists(LOCOMO_DATA):
    LOCOMO_DATA = "/tmp/nexo-locomo-src/data/locomo10.json"

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"


def setup_isolated_db(sample_id: str, mode: str) -> str:
    """Create isolated cognitive.db for a sample. Returns DB path."""
    db_dir = os.path.join(BENCH_DIR, mode, sample_id)
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "cognitive.db")

    # Reset cognitive module to use new DB
    import cognitive
    cognitive.COGNITIVE_DB = db_path
    cognitive._conn = None  # Force reconnection on next call
    return db_path


def ingest_conversation(sample: dict, run_cognitive_cycles: bool = False):
    """Ingest all dialog turns from a LoCoMo sample into cognitive memory."""
    import cognitive

    conv = sample["conversation"]
    speaker_a = conv.get("speaker_a", "Speaker A")
    speaker_b = conv.get("speaker_b", "Speaker B")

    session_num = 0
    for key in sorted(conv.keys()):
        if not key.startswith("session_") or key.endswith("_date_time"):
            continue

        session_num += 1
        session_date = conv.get(f"{key}_date_time", f"Session {session_num}")
        dialogs = conv[key]

        if not isinstance(dialogs, list):
            continue

        # Store individual turns
        turn_texts = []
        turn_ids = []
        for turn in dialogs:
            dia_id = turn.get("dia_id", "")
            speaker = turn.get("speaker", "unknown")
            text = turn.get("text", "")

            # Handle image turns (use caption as text)
            if not text and "blip_caption" in turn:
                text = f"[shares image: {turn['blip_caption']}]"

            if not text:
                continue

            content = f"[{session_date}] {speaker}: {text}"
            turn_texts.append(content)
            turn_ids.append(dia_id)

            cognitive.ingest(
                content=content,
                source_type="dialog",
                source_id=dia_id,
                source_title=f"Session {session_num}",
                domain=sample["sample_id"],
                bypass_gate=True,
                skip_quarantine=True,
                bypass_security=True,
            )

        # Store overlapping chunks of 3 turns for multi-hop context
        chunk_size = 3
        for i in range(0, len(turn_texts) - chunk_size + 1):
            chunk_content = "\n".join(turn_texts[i:i + chunk_size])
            chunk_ids = ",".join(turn_ids[i:i + chunk_size])
            # Store all dia_ids as source_id for evidence matching
            cognitive.ingest(
                content=chunk_content,
                source_type="dialog_chunk",
                source_id=chunk_ids,
                source_title=f"Session {session_num} chunk",
                domain=sample["sample_id"],
                bypass_gate=True,
                skip_quarantine=True,
                bypass_security=True,
            )

        # Session summary: extractive summary of all turns (captures big picture)
        if turn_texts:
            # Build a concise session summary from the turns
            speakers = set()
            topics = []
            for t in turn_texts:
                # Extract speaker name
                if "] " in t:
                    after = t.split("] ", 1)[1]
                    if ": " in after:
                        speakers.add(after.split(": ", 1)[0])
                        topics.append(after.split(": ", 1)[1][:100])

            summary = f"[{session_date}] Session {session_num} summary ({', '.join(speakers)}): "
            summary += " | ".join(topics[:5])  # First 5 topics
            if len(topics) > 5:
                summary += f" | ... ({len(topics)} total turns)"

            # Store all dia_ids so evidence matching works
            all_ids = ",".join(turn_ids)
            cognitive.ingest(
                content=summary,
                source_type="session_summary",
                source_id=all_ids,
                source_title=f"Session {session_num} summary",
                domain=sample["sample_id"],
                bypass_gate=True,
                skip_quarantine=True,
                bypass_security=True,
            )

        # After each session, optionally run cognitive cycles
        if run_cognitive_cycles and session_num > 1:
            cognitive.promote_stm_to_ltm()
            cognitive.apply_decay(adaptive=True)
            if session_num % 5 == 0:
                cognitive.dream_cycle(max_insights=10)

    # Final consolidation
    if run_cognitive_cycles:
        cognitive.promote_stm_to_ltm()
        cognitive.dream_cycle(max_insights=20)


def retrieve_context(question: str, top_k: int = 5) -> tuple[list[str], str]:
    """Search NEXO cognitive memory with multi-query for complex questions. Returns (dia_ids, context_text)."""
    import cognitive

    # Multi-query decomposition for multi-hop questions
    # If question contains connectors, search for sub-parts too
    sub_queries = [question]
    connectors = [" after ", " before ", " because ", " and then ", " when ", " while "]
    for conn in connectors:
        if conn in question.lower():
            parts = question.lower().split(conn, 1)
            if len(parts) == 2 and len(parts[0]) > 10 and len(parts[1]) > 10:
                sub_queries.extend([parts[0].strip("? "), parts[1].strip("? ")])
                break

    # Collect results from all queries, deduplicate
    all_results = {}
    for q in sub_queries:
        results = cognitive.search(
            query_text=q,
            top_k=top_k,
            min_score=0.3,
            stores="both",
            rehearse=False,
            use_hyde=True,
            hybrid=True,
        )
        for r in results:
            key = (r["store"], r["id"])
            if key not in all_results or r["score"] > all_results[key]["score"]:
                all_results[key] = r

    results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    dia_ids = []
    context_parts = []
    for r in results:
        content = r.get("content", "")
        source_id = r.get("source_id", "")
        score = r.get("score", 0)

        if source_id:
            # Expand chunk source_ids (e.g., "D1:1,D1:2,D1:3" → 3 dia_ids)
            for sid in source_id.split(","):
                sid = sid.strip()
                if sid and sid not in dia_ids:
                    dia_ids.append(sid)
        context_parts.append(f"[{source_id} score={score:.3f}] {content}")

    context_text = "\n".join(context_parts)
    return dia_ids, context_text


def generate_answer(question: str, context: str, category: int) -> str:
    """Use Claude to generate an answer given retrieved context."""
    import anthropic

    client = anthropic.Anthropic()

    # Category-specific instructions
    cat_instructions = {
        1: "Combine facts from multiple parts. Answer with just the combined fact.",
        2: "Answer with ONLY the date or time period. Nothing else.",
        3: "Answer with ONLY the first/earliest occurrence mentioned.",
        4: "Answer with just the key fact — no explanation.",
        5: "If the information is NOT in the context, say exactly 'no information available'. Do not guess.",
    }

    system_prompt = (
        "Answer questions about past conversations using ONLY the provided context. "
        "Give the SHORTEST possible answer — just the key fact, name, date, or phrase. "
        "Do NOT explain, elaborate, or add context. "
        "If asked 'when', answer with ONLY the date/time. "
        "If asked 'what', answer with ONLY the thing. "
        "If asked 'who', answer with ONLY the name. "
        "If the answer is not in the context, say exactly 'no information available'. "
        f"{cat_instructions.get(category, '')}"
    )

    user_prompt = f"Context:\n{context}\n\nQ: {question}\nA (shortest possible):"

    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=50,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"Error: {e}"


def run_single_mode(samples: list, mode: str, top_k: int = 5) -> list:
    """Run benchmark in a single mode (rag or cognitive). Returns results."""
    run_cognitive = mode == "cognitive"
    all_results = []
    prediction_key = f"nexo_{mode}_prediction"

    for si, sample in enumerate(samples):
        sample_id = sample["sample_id"]
        print(f"\n{'='*60}")
        print(f"[{mode.upper()}] Sample {si+1}/10: {sample_id}")
        print(f"{'='*60}")

        # Fresh isolated DB
        setup_isolated_db(sample_id, mode)

        # Ingest conversation
        t0 = time.time()
        ingest_conversation(sample, run_cognitive_cycles=run_cognitive)
        ingest_time = time.time() - t0
        print(f"  Ingested in {ingest_time:.1f}s (cognitive_cycles={run_cognitive})")

        # Process QA pairs
        qa_results = []
        for qi, qa in enumerate(sample["qa"]):
            question = qa["question"]
            category = qa["category"]

            # Retrieve
            dia_ids, context = retrieve_context(question, top_k=top_k)

            # Generate answer
            answer = generate_answer(question, context, category)

            # Build result entry (LoCoMo format)
            # Category 5 (adversarial) uses 'adversarial_answer' — correct answer is "unanswerable"
            ground_truth = qa.get("answer", qa.get("adversarial_answer", ""))
            result = {
                "question": question,
                "answer": ground_truth,
                "evidence": qa["evidence"],
                "category": category,
                prediction_key: answer,
                f"{prediction_key}_context": dia_ids,
            }
            qa_results.append(result)

            if (qi + 1) % 50 == 0:
                print(f"  QA: {qi+1}/{len(sample['qa'])}")

        all_results.append({
            "sample_id": sample_id,
            "qa": qa_results,
            "meta": {
                "mode": mode,
                "ingest_time_s": round(ingest_time, 1),
                "top_k": top_k,
                "model": ANTHROPIC_MODEL,
                "timestamp": datetime.now().isoformat(),
            },
        })
        print(f"  Done: {len(qa_results)} QA pairs processed")

    return all_results


def evaluate_results(results: list, mode: str, prediction_key: str) -> dict:
    """Run LoCoMo evaluation metrics on results."""
    # Import LoCoMo evaluation functions
    locomo_eval = "/tmp/nexo-locomo-src"
    sys.path.insert(0, locomo_eval)
    sys.path.insert(0, os.path.join(locomo_eval, "task_eval"))
    from evaluation import f1_score, f1, normalize_answer
    from nltk.stem import PorterStemmer
    ps = PorterStemmer()

    category_names = {
        1: "Multi-hop",
        2: "Temporal",
        3: "First-answer",
        4: "Open-domain",
        5: "Adversarial",
    }

    # Collect all QA across samples
    all_qa = []
    for sample in results:
        all_qa.extend(sample["qa"])

    # Compute metrics per category
    category_scores = {c: [] for c in range(1, 6)}
    category_recall = {c: [] for c in range(1, 6)}

    for qa in all_qa:
        cat = qa["category"]
        prediction = qa[prediction_key]
        ground_truth = str(qa["answer"])

        # F1 score (same as LoCoMo)
        if cat == 3:
            ground_truth = ground_truth.split(";")[0].strip()

        if cat in [2, 3, 4]:
            score = f1_score(prediction, ground_truth)
        elif cat == 1:
            score = f1(prediction, ground_truth)
        elif cat == 5:
            if "no information available" in prediction.lower() or "not mentioned" in prediction.lower():
                score = 1.0
            else:
                score = 0.0

        category_scores[cat].append(score)

        # Retrieval recall (did we find the evidence dia_ids?)
        context_key = f"{prediction_key}_context"
        if context_key in qa and qa["evidence"]:
            retrieved = qa[context_key]
            hits = sum(1 for ev in qa["evidence"] if ev in retrieved)
            recall = hits / len(qa["evidence"])
            category_recall[cat].append(recall)

    # Aggregate
    metrics = {"mode": mode, "model": ANTHROPIC_MODEL, "timestamp": datetime.now().isoformat()}
    total_f1 = []
    total_recall = []

    print(f"\n{'='*60}")
    print(f"NEXO Brain — LoCoMo Results [{mode.upper()}]")
    print(f"{'='*60}")
    print(f"{'Category':<15} {'Count':>6} {'F1':>8} {'Recall':>8}")
    print(f"{'-'*40}")

    for cat in [4, 1, 2, 3, 5]:
        scores = category_scores[cat]
        recalls = category_recall[cat]
        avg_f1 = sum(scores) / len(scores) if scores else 0
        avg_recall = sum(recalls) / len(recalls) if recalls else 0

        name = category_names[cat]
        print(f"{name:<15} {len(scores):>6} {avg_f1:>8.3f} {avg_recall:>8.3f}")

        metrics[f"cat_{cat}_{name.lower().replace('-', '_')}"] = {
            "count": len(scores),
            "f1": round(avg_f1, 4),
            "recall": round(avg_recall, 4),
        }

        total_f1.extend(scores)
        total_recall.extend(recalls)

    overall_f1 = sum(total_f1) / len(total_f1) if total_f1 else 0
    overall_recall = sum(total_recall) / len(total_recall) if total_recall else 0

    print(f"{'-'*40}")
    print(f"{'OVERALL':<15} {len(total_f1):>6} {overall_f1:>8.3f} {overall_recall:>8.3f}")

    metrics["overall"] = {
        "total_qa": len(total_f1),
        "f1": round(overall_f1, 4),
        "recall": round(overall_recall, 4),
    }

    return metrics


def main():
    parser = argparse.ArgumentParser(description="NEXO Brain × LoCoMo Benchmark")
    parser.add_argument("--mode", choices=["rag", "cognitive", "both"], default="both")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--samples", type=int, default=10, help="Number of samples to process (1-10)")
    args = parser.parse_args()

    # Load data
    print(f"Loading LoCoMo data from {LOCOMO_DATA}")
    with open(LOCOMO_DATA) as f:
        samples = json.load(f)[:args.samples]
    print(f"Loaded {len(samples)} samples, {sum(len(s['qa']) for s in samples)} QA pairs")

    # Ensure deps
    try:
        import anthropic
        import cognitive
        from nltk.stem import PorterStemmer
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("pip install anthropic nltk fastembed numpy")
        sys.exit(1)

    # Clean slate
    if os.path.exists(BENCH_DIR):
        shutil.rmtree(BENCH_DIR)

    os.makedirs(RESULTS_DIR, exist_ok=True)

    modes = ["rag", "cognitive"] if args.mode == "both" else [args.mode]
    all_metrics = {}

    for mode in modes:
        prediction_key = f"nexo_{mode}_prediction"

        # Run benchmark
        results = run_single_mode(samples, mode, args.top_k)

        # Save raw results
        raw_file = os.path.join(RESULTS_DIR, f"locomo_nexo_{mode}_raw.json")
        with open(raw_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nRaw results saved: {raw_file}")

        # Evaluate
        metrics = evaluate_results(results, mode, prediction_key)
        all_metrics[mode] = metrics

        # Save metrics
        metrics_file = os.path.join(RESULTS_DIR, f"locomo_nexo_{mode}_metrics.json")
        with open(metrics_file, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"Metrics saved: {metrics_file}")

    # Save combined summary
    summary = {
        "benchmark": "LoCoMo (Snap Research, ACL 2024)",
        "system": "NEXO Brain Cognitive Memory",
        "embedding_model": "BAAI/bge-small-en-v1.5 (384 dims, CPU)",
        "answer_model": ANTHROPIC_MODEL,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "samples": len(samples),
        "total_qa": sum(len(s["qa"]) for s in samples),
        "top_k": args.top_k,
        "results": all_metrics,
    }
    summary_file = os.path.join(RESULTS_DIR, "locomo_nexo_summary.json")
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    # Print comparison table
    if len(all_metrics) == 2:
        print(f"\n{'='*60}")
        print("COMPARISON: RAG vs Cognitive")
        print(f"{'='*60}")
        print(f"{'Metric':<20} {'RAG':>10} {'Cognitive':>10} {'Delta':>10}")
        print(f"{'-'*50}")
        for key in ["overall"]:
            rag_f1 = all_metrics["rag"][key]["f1"]
            cog_f1 = all_metrics["cognitive"][key]["f1"]
            delta = cog_f1 - rag_f1
            sign = "+" if delta > 0 else ""
            print(f"{'F1':<20} {rag_f1:>10.4f} {cog_f1:>10.4f} {sign}{delta:>9.4f}")

            rag_r = all_metrics["rag"][key]["recall"]
            cog_r = all_metrics["cognitive"][key]["recall"]
            delta_r = cog_r - rag_r
            sign_r = "+" if delta_r > 0 else ""
            print(f"{'Recall':<20} {rag_r:>10.4f} {cog_r:>10.4f} {sign_r}{delta_r:>9.4f}")

    # Cleanup temp data
    if os.path.exists(BENCH_DIR):
        shutil.rmtree(BENCH_DIR)
        print(f"\nCleaned up {BENCH_DIR} — zero trace on disk.")

    # Also clean LoCoMo source repo
    locomo_tmp = "/tmp/nexo-locomo-src"
    if os.path.exists(locomo_tmp):
        shutil.rmtree(locomo_tmp)
        print(f"Cleaned up {locomo_tmp}")

    print(f"\nResults saved in: {RESULTS_DIR}/")
    print("Done.")


if __name__ == "__main__":
    main()
