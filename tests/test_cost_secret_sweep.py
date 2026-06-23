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
