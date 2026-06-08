from __future__ import annotations

import unittest

from src.db import _personal_scripts as personal_scripts


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _Rows(self._rows)


class PersonalScriptRunStateTest(unittest.TestCase):
    def setUp(self):
        self._original_get_db = personal_scripts._get_db
        self._original_schedules = personal_scripts.list_personal_script_schedules
        self._original_latest = personal_scripts._latest_cron_runs_by_id

    def tearDown(self):
        personal_scripts._get_db = self._original_get_db
        personal_scripts.list_personal_script_schedules = self._original_schedules
        personal_scripts._latest_cron_runs_by_id = self._original_latest

    def _script_rows(self, *, last_run_at: str, last_exit_code: int):
        return [{
            "id": "ps-orchestrator-v2",
            "name": "nora-orchestrator",
            "path": "/tmp/nora-orchestrator-wrapper.py",
            "description": "Nora",
            "runtime": "python",
            "metadata_json": "{}",
            "created_by": "manual",
            "source": "filesystem",
            "enabled": 1,
            "has_inline_metadata": 1,
            "last_run_at": last_run_at,
            "last_exit_code": last_exit_code,
            "last_synced_at": last_run_at,
            "origin": "user",
            "created_at": last_run_at,
            "updated_at": last_run_at,
        }]

    def _wire(self, *, script_last: str, script_exit: int, cron_last: str, cron_exit: int):
        personal_scripts._get_db = lambda: _Conn(
            self._script_rows(last_run_at=script_last, last_exit_code=script_exit)
        )
        personal_scripts.list_personal_script_schedules = lambda include_disabled=True: [{
            "id": 1,
            "script_id": "ps-orchestrator-v2",
            "cron_id": "nora-orchestrator",
            "enabled": True,
        }]
        personal_scripts._latest_cron_runs_by_id = lambda _cron_ids: {
            "nora-orchestrator": {
                "started_at": cron_last,
                "exit_code": cron_exit,
            }
        }

    def test_manual_run_newer_than_cron_keeps_manual_success(self):
        self._wire(
            script_last="2026-06-08T21:40:08",
            script_exit=0,
            cron_last="2026-06-08 15:40:18",
            cron_exit=1,
        )

        row = personal_scripts.list_personal_scripts()[0]

        self.assertEqual(row["last_run_at"], "2026-06-08T21:40:08")
        self.assertEqual(row["last_exit_code"], 0)

    def test_cron_run_newer_than_manual_keeps_cron_state(self):
        self._wire(
            script_last="2026-06-08T15:40:08",
            script_exit=0,
            cron_last="2026-06-08 21:40:18",
            cron_exit=1,
        )

        row = personal_scripts.list_personal_scripts()[0]

        self.assertEqual(row["last_run_at"], "2026-06-08 21:40:18")
        self.assertEqual(row["last_exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
