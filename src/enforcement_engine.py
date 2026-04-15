"""Headless Protocol Enforcement Engine for NEXO Brain.

Wraps a Claude Code subprocess with stream-json I/O, monitors tool calls,
and injects enforcement prompts when rules from tool-enforcement-map.json
are violated. Python equivalent of Desktop's enforcement-engine.js.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
MAP_FILENAME = "tool-enforcement-map.json"
LOG_DIR = NEXO_HOME / "logs"

_logger = logging.getLogger("nexo.enforcer")
if not _logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(LOG_DIR / "enforcer-headless.log")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _logger.addHandler(_fh)
    _logger.setLevel(logging.INFO)


def _load_map() -> dict | None:
    for candidate in [
        NEXO_HOME / MAP_FILENAME,
        NEXO_HOME / "brain" / MAP_FILENAME,
        Path(__file__).parent.parent / MAP_FILENAME,
    ]:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def _normalize(name: str) -> str:
    return name.replace("mcp__nexo__", "")


class HeadlessEnforcer:
    """Monitor a Claude Code stream-json process and enforce protocol rules."""

    def __init__(self):
        self.map = _load_map()
        self.tools_called: set[str] = set()
        self.tool_call_count = 0
        self.user_message_count = 0
        self.tool_timestamps: dict[str, float] = {}
        self.msg_since_tool: dict[str, int] = {}
        self.injection_queue: list[dict] = []
        self._started_at = time.time()
        self._injections_done = 0

        self._on_start: list[dict] = []
        self._on_end: list[dict] = []
        self._periodic_msg: list[dict] = []
        self._periodic_time: list[dict] = []
        self._after_tool: dict[str, list[dict]] = {}

        if self.map:
            self._build_indexes()
            _logger.info("Map v%s loaded: %d on_start, %d on_end, %d periodic_msg, %d periodic_time, %d after_tool",
                         self.map.get("version", "?"), len(self._on_start), len(self._on_end),
                         len(self._periodic_msg), len(self._periodic_time), len(self._after_tool))
        else:
            _logger.warning("No enforcement map found")

    def _build_indexes(self):
        for tool_name, tool_def in self.map.get("tools", {}).items():
            enf = tool_def.get("enforcement")
            if not enf or enf.get("level") == "none":
                continue
            for rule in enf.get("rules", []):
                rtype = rule.get("type", "")
                entry = {"tool": tool_name, "rule": rule, "enf": enf}
                if rtype == "on_session_start":
                    self._on_start.append(entry)
                elif rtype == "on_session_end":
                    self._on_end.append(entry)
                elif rtype == "periodic_by_messages":
                    self._periodic_msg.append(entry)
                elif rtype == "periodic_by_time":
                    self._periodic_time.append(entry)
                elif rtype == "after_tool":
                    for wt in rule.get("watch_tools", []):
                        self._after_tool.setdefault(wt, []).append(entry)

            for triggered in enf.get("triggers_after", []):
                self._after_tool.setdefault(tool_name, []).append({
                    "tool": triggered,
                    "rule": {"type": "after_tool"},
                    "enf": self.map["tools"].get(triggered, {}).get("enforcement", {}),
                })

    def on_tool_call(self, raw_name: str):
        name = _normalize(raw_name)
        self.tool_call_count += 1
        self.tools_called.add(name)
        self.tool_timestamps[name] = time.time()
        self.msg_since_tool[name] = 0
        _logger.info("TOOL_CALL #%d: %s", self.tool_call_count, name)

        for entry in self._after_tool.get(name, []):
            target = entry["tool"]
            if target not in self.tools_called:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"after:{name}->{target}")

    def check_periodic(self):
        for entry in self._on_start:
            tool = entry["tool"]
            threshold = entry["rule"].get("threshold", 2)
            if tool not in self.tools_called and self.tool_call_count >= threshold:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"start:{tool}")

        for entry in self._periodic_msg:
            tool = entry["tool"]
            threshold = entry["rule"].get("threshold", 3)
            count = self.msg_since_tool.get(tool, self.user_message_count)
            if count >= threshold:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"periodic_msg:{tool}")

        for entry in self._periodic_time:
            tool = entry["tool"]
            threshold_min = entry["rule"].get("threshold", 15)
            last = self.tool_timestamps.get(tool, self._started_at)
            elapsed_min = (time.time() - last) / 60
            if elapsed_min >= threshold_min:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"periodic_time:{tool}")

    def get_end_prompts(self) -> list[str]:
        prompts = []
        for entry in self._on_end:
            if entry["enf"].get("level") == "must":
                p = entry["enf"].get("session_end_inject_prompt") or entry["enf"].get("inject_prompt", "")
                if p:
                    prompts.append(p)
        _logger.info("END_PROMPTS: %d prompts to inject", len(prompts))
        return prompts

    def flush(self) -> dict | None:
        if not self.injection_queue:
            return None
        return self.injection_queue.pop(0)

    def _enqueue(self, prompt: str, tag: str):
        if any(q["tag"] == tag for q in self.injection_queue):
            return
        tool = tag.split(":")[-1].split("->")[-1]
        last_called = self.tool_timestamps.get(tool)
        if last_called and tool in self.tools_called:
            if time.time() - last_called < 60:
                _logger.info("DEDUP_SKIP: %s — %s called %ds ago", tag, tool, int(time.time() - last_called))
                return
        if tool in self.tools_called and not tag.startswith("periodic_"):
            _logger.info("SKIP: %s — already called", tag)
            return
        self.injection_queue.append({"prompt": prompt, "tag": tag, "at": time.time()})
        _logger.info("ENQUEUED: %s (queue size: %d)", tag, len(self.injection_queue))

    def summary(self) -> str:
        return (f"tools_called={len(self.tools_called)} tool_calls={self.tool_call_count} "
                f"injections={self._injections_done} tools={sorted(self.tools_called)}")


def run_with_enforcement(
    cmd: list[str],
    *,
    prompt: str,
    cwd: str = "",
    env: dict | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    enforcer = HeadlessEnforcer()
    _logger.info("=== SESSION START === prompt=%s timeout=%d", prompt[:80], timeout)

    if not enforcer.map:
        _logger.warning("No map — falling back to plain subprocess.run")
        return subprocess.run(cmd, cwd=cwd or None, capture_output=True, text=True,
                              timeout=timeout, env=env)

    stream_cmd = []
    skip_next = False
    for i, arg in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        if arg == "-p":
            skip_next = True
            continue
        if arg == "--output-format":
            skip_next = True
            continue
        stream_cmd.append(arg)

    stream_cmd.extend([
        "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ])

    proc = subprocess.Popen(
        stream_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd or None,
        env=env,
        text=True,
    )

    initial_msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}
    })
    proc.stdin.write(initial_msg + "\n")
    proc.stdin.flush()

    collected_text = []
    stderr_lines = []
    start_time = time.time()
    waiting_for_injection_response = False

    def _inject(text: str):
        nonlocal waiting_for_injection_response
        msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}
        })
        try:
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
            waiting_for_injection_response = True
            enforcer._injections_done += 1
            _logger.info("INJECTED: %s", text[:100])
        except Exception as e:
            _logger.error("INJECT_FAILED: %s", e)

    def _read_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    last_periodic_check = time.time()

    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            if time.time() - start_time > timeout:
                _logger.warning("TIMEOUT after %ds", timeout)
                proc.kill()
                break

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "assistant" and event.get("message", {}).get("content"):
                for block in event["message"]["content"]:
                    if block.get("type") == "tool_use":
                        enforcer.on_tool_call(block.get("name", ""))
            elif event_type == "content_block_start":
                cb = event.get("content_block", {})
                if cb.get("type") == "tool_use":
                    enforcer.on_tool_call(cb.get("name", ""))

            if event_type == "assistant" and not waiting_for_injection_response:
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        collected_text.append(block["text"])

            if event_type == "result":
                if waiting_for_injection_response:
                    waiting_for_injection_response = False
                    _logger.info("INJECTION_RESPONSE received")
                    item = enforcer.flush()
                    if item:
                        _inject(item["prompt"])
                    continue

                enforcer.check_periodic()
                item = enforcer.flush()
                if item:
                    _inject(item["prompt"])
                else:
                    _logger.info("TURN_END — no pending enforcements, done")
                    break

            if time.time() - last_periodic_check > 30:
                enforcer.check_periodic()
                last_periodic_check = time.time()

    except Exception as e:
        _logger.error("EXCEPTION: %s", e)
    finally:
        end_prompts = enforcer.get_end_prompts()
        for ep in end_prompts:
            try:
                _inject(ep)
                deadline = time.time() + 15
                for raw_line in proc.stdout:
                    if time.time() > deadline:
                        _logger.warning("END_PROMPT timeout")
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") == "result":
                            _logger.info("END_PROMPT response received")
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception:
                break

        elapsed = time.time() - start_time
        _logger.info("=== SESSION END === duration=%.1fs %s", elapsed, enforcer.summary())

        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    stderr_thread.join(timeout=2)
    final_text = "\n".join(collected_text)
    final_stderr = "".join(stderr_lines)

    return subprocess.CompletedProcess(
        stream_cmd, proc.returncode or 0, final_text, final_stderr
    )
