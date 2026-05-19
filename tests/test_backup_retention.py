from __future__ import annotations

import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_backup_retention_plan_is_json_and_preserves_protected_restore_points(tmp_path):
    import backup_retention

    root = tmp_path / "backups"
    _write(root / "pre-update-old" / "marker.txt")
    _write(root / "shopify-backups" / "order.csv", "business")
    _write(root / "weekly" / "weekly-2026-05-19.db", "weekly")
    _write(root / "nexo-2026-05-19-1829.db", "hourly")

    plan = backup_retention.backup_retention_plan(
        root=root,
        max_bytes="1",
    )

    assert plan["ok"] is True
    assert plan["policy"]["restore_point_guard"]["hourly_db_present"] == 1
    assert plan["policy"]["restore_point_guard"]["weekly_present"] is True
    assert plan["policy"]["restore_point_guard"]["protected_delete_violations"] == []
    assert "shopify-backups" not in {item["name"] for item in plan["delete"]}
    assert "nexo-2026-05-19-1829.db" not in {item["name"] for item in plan["delete"]}


def test_backup_retention_apply_delete_all_technical_keeps_business_weekly_and_hourly(tmp_path):
    import backup_retention

    root = tmp_path / "backups"
    _write(root / "pre-update-old" / "marker.txt")
    _write(root / "code-tree-old" / "marker.txt")
    _write(root / "shopify-backups" / "order.csv", "business")
    _write(root / "weekly" / "weekly-2026-05-19.db", "weekly")
    _write(root / "nexo-2026-05-19-1829.db", "hourly")

    result = backup_retention.backup_retention_apply(
        root=root,
        max_bytes="0",
        delete_all_technical=True,
    )

    assert result["ok"] is True
    assert result["apply"]["deleted"] == 2
    assert not (root / "pre-update-old").exists()
    assert not (root / "code-tree-old").exists()
    assert (root / "shopify-backups").is_dir()
    assert (root / "weekly").is_dir()
    assert (root / "nexo-2026-05-19-1829.db").is_file()
