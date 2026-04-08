# NEXO in 5 Minutes

NEXO is the local cognitive runtime that makes the model around your model smarter.

## 1. Install

```bash
npx nexo-brain init
```

That installs the runtime, configures the MCP server, and wires the shared brain into the supported clients.

## 2. Verify the runtime

```bash
nexo doctor
python3 scripts/verify_client_parity.py
```

You want the runtime clean enough that `doctor` is not `critical`, and parity checks should pass before claiming the shared-brain layer is healthy.

## 3. Use the minimal public mental model

Memory:

```bash
nexo call nexo_remember --input '{"content":"Release tasks must close with evidence.","title":"Release evidence rule","domain":"nexo"}'
nexo call nexo_memory_recall --input '{"query":"release evidence"}'
```

Consolidation:

```bash
nexo call nexo_consolidate --input '{"max_insights":8}'
```

Durable execution:

```bash
nexo call nexo_run_workflow --input '{
  "sid":"YOUR_SESSION_ID",
  "goal":"Prepare v3.0 release",
  "steps":"[{\"step_key\":\"doctor\",\"title\":\"Run doctor\"},{\"step_key\":\"package\",\"title\":\"Package release\"}]"
}'
```

For practical `open` / `update` / `resume` / `replay` examples, see [Workflow Quickstart](./workflows-quickstart.md).

For 24-hour recent continuity across sessions and clients, see [Hot Context Memory](./hot-context-memory.md).

## 4. Use the protocol path for real work

For anything non-trivial:

```bash
nexo call nexo_task_open --input '{"sid":"YOUR_SESSION_ID","goal":"Patch release bug","task_type":"edit","area":"nexo","files":["/abs/path/file.py"]}'
```

Close it with evidence:

```bash
nexo call nexo_task_close --input '{"sid":"YOUR_SESSION_ID","task_id":"PT-...","outcome":"done","evidence":"pytest -q ... passed","files_changed":["/abs/path/file.py"]}'
```

Optional strictness for new users lives in `NEXO_HOME/brain/calibration.json`:

```json
{
  "preferences": {
    "protocol_strictness": "learning"
  }
}
```

Modes:

- `lenient`: current default, warnings/debt first
- `strict`: block writes before they happen if there is no open protocol task
- `learning`: same block, but with a more explanatory message

## 5. Generate the public scorecard

```bash
python3 scripts/build_public_scorecard.py
```

This writes measured compare artifacts to `compare/scorecard.json` and `compare/README.md`.

## More setup paths

- Docker / persistent container runtime: [docker-setup.md](./docker-setup.md)
- Cursor companion setup: [integrations/cursor.md](./integrations/cursor.md)
- Windsurf companion setup: [integrations/windsurf.md](./integrations/windsurf.md)
- Gemini CLI adapter: [../adapters/gemini/README.md](../adapters/gemini/README.md)
- Workflow examples: [workflows-quickstart.md](./workflows-quickstart.md)
