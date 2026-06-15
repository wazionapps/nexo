#!/usr/bin/env python3
"""Deterministic golden-set generator for the Ola 1 memory eval bench.

Materialises a small, fully reproducible corpus + query set with ground-truth
``relevant_uids`` so the harness can compute recall@k/MRR offline, with NO LLM
and NO production data. Three query families, each tied to a distinct Ola 1
claim:

  - ``paraphrase``  : query wording differs from the observation wording.
                      Measures the *semantic gain* the FTS+vector fusion adds.
  - ``lexical``     : query reuses distinctive tokens from the observation.
                      A no-regression control: the lexical/FTS path must keep
                      acing these even after vector fusion was added.
  - ``abstention``  : query about something NOT in the corpus.
                      The system is correct iff it returns nothing relevant.

Determinism contract
--------------------
Everything is seeded from ``SEED`` (a fixed string). The same SEED always
produces byte-identical JSONL, so ``manifest.json`` carries a SHA-256 over the
serialised records. Re-running ``generate.py`` and diffing the hash proves
reproducibility; the harness and the pytest both assert on it.

Usage
-----
    python benchmarks/golden/generate.py            # write JSONL + manifest
    python benchmarks/golden/generate.py --check    # verify on-disk hash only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OBSERVATIONS_PATH = os.path.join(HERE, "observations.jsonl")
QUERIES_PATH = os.path.join(HERE, "queries.jsonl")
MANIFEST_PATH = os.path.join(HERE, "manifest.json")

SEED = "ola1-golden-v1"
CASE_SET_ID = "ola1-golden"
CASE_SET_VERSION = "2026-06-15"

# A fixed reference epoch so created_at is deterministic (not "now"). These
# observations are seeded into an isolated /tmp DB, never production.
_BASE_TS = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc).timestamp()

# ---------------------------------------------------------------------------
# Source facts. Each tuple is:
#   (slug, subject, summary, paraphrase_query, lexical_query)
# - ``summary`` is what gets stored (what retrieval indexes).
# - ``paraphrase_query`` deliberately avoids the rare tokens of ``summary``.
# - ``lexical_query`` reuses a distinctive token from ``summary``.
# Facts are realistic NEXO-operations style so the bench resembles real recall.
# ---------------------------------------------------------------------------
_FACTS: list[tuple[str, str, str, str, str]] = [
    (
        "playwright-chromium",
        "Recambios review badges",
        "The weekly Shopify health cron failed silently because the Playwright "
        "chromium browser was deleted after upgrading Homebrew to Python 3.14.",
        "why did the store rating widgets stop updating for several weeks",
        "playwright chromium browser deleted",
    ),
    (
        "wazion-shopid-na",
        "WAzion WhatsApp connect",
        "WAzion shows Shop ID N/A when the web panel session lost its "
        "authenticated_id_shop, so connect.php forwards an empty shop_id.",
        "the WhatsApp QR pairing complains the account id is missing",
        "authenticated_id_shop empty shop_id",
    ),
    (
        "openai-key-leak",
        "OpenAI cost spike",
        "The OpenAI cost spike was caused by a compromised global API key issuing "
        "about ninety thousand gpt-5.5 responses; it was rotated to isolated keys.",
        "spending on the language model provider jumped suddenly one day",
        "compromised global API key rotated isolated",
    ),
    (
        "newsletter-pixel",
        "Newsletter suppression bug",
        "Inactive subscribers were wrongly suppressed because the marker counted "
        "only the open pixel and ignored real clicks, hiding engaged readers.",
        "engaged readers stopped receiving the mailing because of tracking",
        "open pixel ignored real clicks suppressed",
    ),
    (
        "vin-session-rotation",
        "VIN decoder WhatsApp",
        "The VIN decoder stopped sending diagram messages because it used a fixed "
        "session_key that rotates; resolving the active session from the DB fixed it.",
        "the parts diagram never arrived over chat for a few days",
        "fixed session_key rotates active session",
    ),
    (
        "wsl-unregister",
        "WSL data loss",
        "Running wsl --unregister physically deletes the associated ext4 vhdx file, "
        "so always copy the disk image before inspecting it.",
        "the Linux subsystem lost all its files after a cleanup command",
        "wsl unregister deletes ext4 vhdx",
    ),
    (
        "dnssec-firebase",
        "DNSSEC Firebase block",
        "A signed DNSSEC zone without a DS record at the parent breaks Firebase "
        "Hosting custom domains with a persistent DNS_SERVFAIL.",
        "the website custom domain refused to verify with a resolver failure",
        "DNSSEC DS record DNS_SERVFAIL firebase",
    ),
    (
        "grw-geocoding-key",
        "grwellness geocoding",
        "The dictated address never geocoded because a browser key with a referer "
        "restriction returns REQUEST_DENIED for server-side geocoding calls.",
        "the spoken street name could not be turned into coordinates on the server",
        "browser key referer REQUEST_DENIED geocoding",
    ),
    (
        "cron-exit-zero",
        "Silent cron failure",
        "A weekly cron looked healthy at exit code zero while its wrapper swallowed "
        "the real crash with a fallback echo, masking the broken scrape for weeks.",
        "a scheduled job reported success but produced no real output",
        "cron exit code zero wrapper swallowed",
    ),
    (
        "stripe-tax-canarias",
        "Stripe tax rules",
        "Stripe Tax applies seven percent IGIC for the Canary Islands, zero percent "
        "under article 21 for the peninsula, and reverse charge inside the EU.",
        "how the payment processor handles sales tax for the islands and mainland",
        "Stripe Tax IGIC Canary Islands reverse charge",
    ),
]

# Topics the corpus has NO answer for. These drive the abstention family: the
# correct behaviour is to surface nothing relevant.
_ABSTENTION_QUERIES: list[tuple[str, str]] = [
    ("abstain-kubernetes", "how do we autoscale the kubernetes cluster nodes"),
    ("abstain-payroll", "what is the monthly payroll run schedule for employees"),
    ("abstain-bluetooth", "which bluetooth speaker firmware version do we ship"),
    ("abstain-quantum", "how is the quantum encryption key exchange configured"),
    ("abstain-warehouse", "where is the physical warehouse inventory audit stored"),
    ("abstain-podcast", "what microphone do we use for recording the podcast"),
    ("abstain-tax-japan", "how do we file consumption tax returns in japan"),
    ("abstain-drone", "what is the maximum flight altitude of the delivery drone"),
    ("abstain-fontlicense", "which commercial font license covers the print catalog"),
    ("abstain-solarpanel", "how many solar panels power the office rooftop array"),
]

_PROJECT = "ola1-bench"


def _uid(slug: str) -> str:
    return f"ola1-obs-{slug}"


def build_records() -> tuple[list[dict], list[dict]]:
    """Build (observations, queries) deterministically from SEED.

    The RNG only drives *noise observation* selection and stable ordering, so
    the relevant_uids ground truth is fully fixed. Returns plain dicts ready to
    serialise to JSONL.
    """
    rng = random.Random(SEED)

    observations: list[dict] = []
    for index, (slug, subject, summary, _para, _lex) in enumerate(_FACTS):
        observations.append(
            {
                "observation_uid": _uid(slug),
                "project_key": _PROJECT,
                "observation_type": "fact",
                "subject": subject,
                "summary": summary,
                "salience": 0.6,
                "confidence": 0.8,
                # Spread timestamps deterministically, one hour apart.
                "created_at": _BASE_TS + index * 3600,
                "evidence_refs": [f"golden:{slug}"],
            }
        )

    queries: list[dict] = []
    for slug, _subject, _summary, para, lex in _FACTS:
        queries.append(
            {
                "id": f"q-para-{slug}",
                "text": para,
                "intent": "paraphrase",
                "relevant_uids": [_uid(slug)],
            }
        )
        queries.append(
            {
                "id": f"q-lex-{slug}",
                "text": lex,
                "intent": "lexical",
                "relevant_uids": [_uid(slug)],
            }
        )
    for qid, text in _ABSTENTION_QUERIES:
        queries.append(
            {
                "id": f"q-{qid}",
                "text": text,
                "intent": "abstention",
                "relevant_uids": [],
            }
        )

    # Deterministic shuffle so order is fixed but not grouped by family (avoids
    # any accidental ordering bias in the harness). rng is seeded => stable.
    rng.shuffle(queries)
    return observations, queries


def _serialise_jsonl(records: list[dict]) -> str:
    """Stable JSONL: sorted keys, no trailing whitespace, '\\n' separated."""
    return "".join(
        json.dumps(rec, ensure_ascii=True, sort_keys=True) + "\n" for rec in records
    )


def compute_hash(observations: list[dict], queries: list[dict]) -> str:
    """SHA-256 over the canonical JSONL of both files — the repro fingerprint."""
    blob = _serialise_jsonl(observations) + "\x1e" + _serialise_jsonl(queries)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_golden() -> dict:
    """Generate and write observations.jsonl, queries.jsonl, manifest.json."""
    observations, queries = build_records()
    fixture_hash = compute_hash(observations, queries)

    with open(OBSERVATIONS_PATH, "w", encoding="utf-8") as fh:
        fh.write(_serialise_jsonl(observations))
    with open(QUERIES_PATH, "w", encoding="utf-8") as fh:
        fh.write(_serialise_jsonl(queries))

    manifest = {
        "case_set_id": CASE_SET_ID,
        "case_set_version": CASE_SET_VERSION,
        "seed": SEED,
        "fixture_kind": "synthetic",
        "fixture_hash": fixture_hash,
        "n_observations": len(observations),
        "n_queries": len(queries),
        "n_paraphrase": sum(1 for q in queries if q["intent"] == "paraphrase"),
        "n_lexical": sum(1 for q in queries if q["intent"] == "lexical"),
        "n_abstention": sum(1 for q in queries if q["intent"] == "abstention"),
        "project_key": _PROJECT,
        "generator": "benchmarks/golden/generate.py",
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return manifest


def load_golden() -> tuple[list[dict], list[dict], dict]:
    """Read observations, queries and manifest from disk (for the harness)."""
    with open(OBSERVATIONS_PATH, encoding="utf-8") as fh:
        observations = [json.loads(line) for line in fh if line.strip()]
    with open(QUERIES_PATH, encoding="utf-8") as fh:
        queries = [json.loads(line) for line in fh if line.strip()]
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return observations, queries, manifest


def check_on_disk() -> bool:
    """Return True if the on-disk files match the freshly recomputed hash."""
    observations, queries, manifest = load_golden()
    recomputed = compute_hash(observations, queries)
    return recomputed == manifest.get("fixture_hash")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Ola 1 golden set")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the on-disk hash matches; do not rewrite files",
    )
    args = parser.parse_args()

    if args.check:
        ok = check_on_disk()
        print("OK" if ok else "HASH MISMATCH")
        return 0 if ok else 1

    manifest = write_golden()
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
