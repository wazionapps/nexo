#!/usr/bin/env python3
"""NEXO Cognitive Decay — Daily Ebbinghaus sweep + STM→LTM promotion."""

import json
import sys
from pathlib import Path
from datetime import datetime
import os

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
sys.path.insert(0, str(NEXO_HOME / "src"))
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

    # 1. Apply decay
    cognitive.apply_decay()
    print(f"[{ts}] Decay applied.")

    # 2. Promote eligible STM → LTM
    promoted = cognitive.promote_stm_to_ltm()
    print(f"[{ts}] Promoted {promoted} STM memories to LTM.")

    # 3. Garbage collect expired STM
    gc_count = cognitive.gc_stm()
    print(f"[{ts}] GC: removed {gc_count} expired STM memories.")

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

    # 6. Stats
    stats = cognitive.get_stats()
    print(f"[{ts}] STM: {stats['stm_active']} | LTM: {stats['ltm_active']} active, {stats['ltm_dormant']} dormant")
    print(f"[{ts}] Done.")

    update_catchup_state()


if __name__ == "__main__":
    main()
