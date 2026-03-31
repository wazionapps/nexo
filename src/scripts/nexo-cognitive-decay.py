#!/usr/bin/env python3
"""NEXO Cognitive Decay — Daily Ebbinghaus sweep + STM→LTM promotion."""

import json
import os
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(NEXO_HOME)))
from datetime import datetime, timedelta

sys.path.insert(0, str(NEXO_CODE))
import cognitive

STATE_FILE = NEXO_HOME / "operations" / ".catchup-state.json"


def update_catchup_state():
    """Register successful run so catch-up script knows we ran."""
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        state = {}
    state["cognitive-decay"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Cognitive decay starting...")

    # 0. Process quarantine FIRST — promote/reject/expire pending items
    #    BUG FIX 26-Mar-2026: quarantine was NEVER processed automatically.
    #    78 items were stuck as pending indefinitely.
    try:
        q_result = cognitive.process_quarantine()
        print(f"[{ts}] Quarantine: {q_result['promoted']} promoted, {q_result['rejected']} rejected, "
              f"{q_result['expired']} expired, {q_result['still_pending']} still pending.")
    except Exception as e:
        print(f"[{ts}] Quarantine processing error: {e}")

    # 0b. Purge test/dev memories from STM
    try:
        test_purged = cognitive.gc_test_memories()
        if test_purged > 0:
            print(f"[{ts}] Purged {test_purged} test/dev memories from STM.")
    except Exception as e:
        print(f"[{ts}] Test memory purge error: {e}")

    # 1. Apply decay
    cognitive.apply_decay()
    print(f"[{ts}] Decay applied.")

    # 2. Promote eligible STM → LTM
    promoted = cognitive.promote_stm_to_ltm()
    print(f"[{ts}] Promoted {promoted} STM memories to LTM.")

    # 3. Garbage collect expired STM + sensory
    gc_count = cognitive.gc_stm()
    try:
        gc_sensory = cognitive.gc_sensory(max_age_hours=48)
        print(f"[{ts}] GC: removed {gc_count} expired STM, {gc_sensory} expired sensory.")
    except Exception as e:
        print(f"[{ts}] GC: removed {gc_count} expired STM. Sensory GC error: {e}")

    # 4. Semantic consolidation — merge near-duplicate LTM (cosine > 0.9)
    #    With discriminative fusion: siblings (different environments) are linked, not merged
    try:
        result = cognitive.consolidate_semantic(threshold=0.9, dry_run=False)
        merged = result.get("merged", [])
        siblings = result.get("siblings", [])
        if merged:
            print(f"[{ts}] Consolidated {len(merged)} duplicate LTM pairs:")
            for m in merged[:10]:
                print(f"[{ts}]   [{m['score']}] kept #{m['keep_id']} ({m['keep_access']} accesses), merged #{m['drop_id']}")
        if siblings:
            print(f"[{ts}] Linked {len(siblings)} sibling pairs (similar-but-incompatible):")
            for s in siblings[:10]:
                print(f"[{ts}]   [{s['score']}] #{s['memory_a_id']} <> #{s['memory_b_id']} differ in: {', '.join(s['discriminators'])}")
        if not merged and not siblings:
            print(f"[{ts}] No semantic duplicates or siblings found (threshold=0.9)")
    except Exception as e:
        print(f"[{ts}] Consolidation error: {e}")

    # 5. Correction fatigue — mark memories corrected 3+ times as unreliable
    try:
        fatigued = cognitive.check_correction_fatigue()
        if fatigued:
            print(f"[{ts}] CORRECTION FATIGUE: {len(fatigued)} memories corrected 3+ times in 7d:")
            for f in fatigued:
                print(f"[{ts}]   LTM #{f['memory_id']} ({f['corrections_7d']}x): {f['content'][:80]}...")
        else:
            print(f"[{ts}] No correction fatigue detected.")
    except Exception as e:
        print(f"[{ts}] Correction fatigue check error: {e}")

    # 6. Memory Dreaming — discover hidden connections between recent memories
    try:
        dream_result = cognitive.dream_cycle(max_insights=15)
        scanned = dream_result["memories_scanned"]
        created = dream_result["insights_created"]
        candidates = dream_result["candidates_found"]
        print(f"[{ts}] Dream cycle: scanned {scanned} recent memories, {candidates} candidates, {created} insights created.")
        for insight in dream_result["insights"][:10]:
            print(f"[{ts}]   [{insight['similarity']}] {insight['title_a'][:40]} <-> {insight['title_b'][:40]}")
    except Exception as e:
        print(f"[{ts}] Dream cycle error: {e}")

    # 7. Auto-merge duplicates (runs AFTER dream_cycle, higher threshold than consolidation)
    try:
        merge_result = cognitive.auto_merge_duplicates(threshold=0.92)
        if merge_result["merged"] > 0:
            print(f"[{ts}] Auto-merge: scanned {merge_result['scanned']}, merged {merge_result['merged']} duplicates, {merge_result['kept']} kept.")
            for m in merge_result["merge_log"][:10]:
                print(f"[{ts}]   [{m['similarity']}] kept #{m['kept_id']}, dropped #{m['dropped_id']}")
        else:
            print(f"[{ts}] Auto-merge: scanned {merge_result['scanned']}, no duplicates above 0.92 threshold.")
    except Exception as e:
        print(f"[{ts}] Auto-merge error: {e}")

    # 9. Adaptive weight learning — Ridge regression from feedback-annotated entries
    try:
        sys.path.insert(0, str(NEXO_CODE / "plugins"))
        from adaptive_mode import learn_weights, prune_adaptive_log, check_weight_rollback

        rollback = check_weight_rollback()
        if rollback["status"] == "rolled_back":
            print(f"[{ts}] WEIGHT ROLLBACK: {rollback['reason']}")
        elif rollback["status"] == "ok":
            print(f"[{ts}] Weight health: pre={rollback['pre_rate']}/day, post={rollback['post_rate']}/day")
        elif rollback["status"] != "no_learned_weights":
            print(f"[{ts}] Weight rollback: {rollback['status']}")

        result = learn_weights()
        if result["status"] in ("shadow", "active"):
            mode_label = "SHADOW" if result["status"] == "shadow" else "ACTIVE"
            print(f"[{ts}] Learned weights ({mode_label}) from {result['samples']} samples. Max drift: {result['max_drift']:.4f}")
            for signal, weight in result["weights"].items():
                drift = result["drift"][signal]
                arrow = "+" if drift > 0 else "" if drift < 0 else "="
                print(f"[{ts}]   {signal}: {weight:.4f} ({arrow}{drift:.4f} from static)")
        elif result["status"] == "insufficient_data":
            print(f"[{ts}] Weight learning: {result['samples']}/{result['min_required']} samples (waiting)")
        else:
            print(f"[{ts}] Weight learning: {result['status']}")

        pruned = prune_adaptive_log(max_age_days=90)
        if pruned > 0:
            print(f"[{ts}] Pruned {pruned} adaptive_log entries >90 days")
    except Exception as e:
        print(f"[{ts}] Adaptive weight learning error: {e}")

    # 10. Project somatic events from nexo.db -> cognitive.db
    try:
        projected = cognitive.somatic_project_events()
        if projected > 0:
            print(f"[{ts}] Somatic projection: {projected} events projected to cognitive.db")
    except Exception as e:
        print(f"[{ts}] Somatic projection error: {e}")

    # 11. Somatic marker nightly decay
    try:
        decayed = cognitive.somatic_nightly_decay(gamma=0.95)
        print(f"[{ts}] Somatic decay: {decayed} markers processed (x0.95)")
    except Exception as e:
        print(f"[{ts}] Somatic decay error: {e}")

    # 8. Stats
    stats = cognitive.get_stats()
    print(f"[{ts}] STM: {stats['stm_active']} active (+{stats.get('stm_promoted', 0)} promoted, {stats.get('stm_total', 0)} total) | LTM: {stats['ltm_active']} active, {stats['ltm_dormant']} dormant")
    print(f"[{ts}] Done.")

    update_catchup_state()


if __name__ == "__main__":
    main()
