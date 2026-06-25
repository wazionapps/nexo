import json
import subprocess
import sys
from pathlib import Path

from cost_secret_sweep import build_sweep_queue, collect_text_sources, redact_secrets, run_sweep


def test_queue_prioritizes_exposure_and_impact():
    queue = build_sweep_queue(
        [
            {"source": "a", "text": "billing agotado en OpenAI"},
            {"source": "b", "text": "log token=abc123456789 expuesto", "economic_impact": 3, "exposure": 3},
        ]
    )
    assert queue[0].source == "b"
    assert queue[0].priority == 9


def test_redacts_secret_values():
    text = redact_secrets("token=abc123456789 password=supersecret")
    assert "abc123456789" not in text
    assert "supersecret" not in text


def test_collect_text_sources_reads_globs(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("billing agotado", encoding="utf-8")
    records = collect_text_sources([str(tmp_path / "*.log")])
    assert records[0]["source"] == str(path)


def test_run_sweep_returns_single_queue():
    report = run_sweep(records=[{"source": "f", "text": "rotar token expuesto"}])
    assert report["count"] == 1
    assert report["queue"][0]["category"] == "rotation_pending"


def test_cross_line_words_do_not_false_positive():
    """Trigger words on UNRELATED lines must not be conflated into a hit.

    Regression for the rotation_pending false positive: a HN-style log where
    "public" and "Token" appear on different, unrelated lines should NOT be
    flagged, because no single line carries both required token groups.
    """
    text = (
        "Generating comment for: Notion leaks email of any public page (231pts)\n"
        "some unrelated middle line with nothing relevant here\n"
        "Generating comment for: Claude Token Counter, model comparisons (145pts)\n"
    )
    queue = build_sweep_queue([{"source": "hn-karma.log", "text": text}])
    assert queue == []


def test_same_line_rotation_still_matches():
    """A genuine pending-rotation line (both groups together) must still hit."""
    text = (
        "[2026-04-22 07:00:00] [WARN] followups overdue: Rotar token Shopify "
        "shpat_* pendiente en Shopify Admin\n"
    )
    queue = build_sweep_queue([{"source": "self-audit.log", "text": text}])
    assert len(queue) == 1
    assert queue[0].category == "rotation_pending"
    # summary is the matching LINE, not the whole blob
    assert "Rotar token Shopify" in queue[0].summary


def test_cli_writes_jsonl(tmp_path):
    source = tmp_path / "scan.log"
    source.write_text("billing agotado", encoding="utf-8")
    output = tmp_path / "out.jsonl"
    script = Path(__file__).resolve().parents[1] / "src" / "scripts" / "cost_secret_sweep.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--path", str(source), "--output", str(output)],
        text=True,
        capture_output=True,
        check=True,
    )
    printed = json.loads(proc.stdout)
    written = json.loads(output.read_text(encoding="utf-8").strip())
    assert printed["count"] == 1
    assert written["count"] == 1
