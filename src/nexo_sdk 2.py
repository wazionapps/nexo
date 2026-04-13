"""Minimal Python SDK for the public NEXO mental model."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class NEXOClient:
    """Tiny Python wrapper around `nexo call` for common public operations."""

    nexo_bin: str = "nexo"

    def call(self, tool: str, payload: dict | None = None) -> dict | list | str:
        result = subprocess.run(
            [
                self.nexo_bin,
                "call",
                tool,
                "--input",
                json.dumps(payload or {}, ensure_ascii=False),
                "--json-output",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"{tool} failed").strip())
        text = (result.stdout or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}

    def remember(
        self,
        content: str,
        *,
        title: str = "",
        domain: str = "",
        source_type: str = "note",
        tags: str = "",
        bypass_gate: bool = True,
    ) -> dict | list | str:
        return self.call(
            "nexo_remember",
            {
                "content": content,
                "title": title,
                "domain": domain,
                "source_type": source_type,
                "tags": tags,
                "bypass_gate": bypass_gate,
            },
        )

    def recall(self, query: str, *, days: int = 30) -> dict | list | str:
        return self.call("nexo_memory_recall", {"query": query, "days": days})

    def consolidate(
        self,
        *,
        max_insights: int = 12,
        threshold: float = 0.9,
        dry_run: bool = False,
    ) -> dict | list | str:
        return self.call(
            "nexo_consolidate",
            {
                "max_insights": max_insights,
                "threshold": threshold,
                "dry_run": dry_run,
            },
        )

    def run_workflow(
        self,
        sid: str,
        goal: str,
        *,
        steps: list[dict] | str,
        goal_id: str = "",
        shared_state: dict | str | None = None,
        owner: str = "",
        idempotency_key: str = "",
    ) -> dict | list | str:
        return self.call(
            "nexo_run_workflow",
            {
                "sid": sid,
                "goal": goal,
                "steps": steps if isinstance(steps, str) else json.dumps(steps, ensure_ascii=False),
                "goal_id": goal_id,
                "shared_state": shared_state if isinstance(shared_state, str) else json.dumps(shared_state or {}, ensure_ascii=False),
                "owner": owner,
                "idempotency_key": idempotency_key,
            },
        )
