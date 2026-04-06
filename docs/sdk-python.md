# Python SDK

NEXO now ships a minimal Python wrapper in [`src/nexo_sdk.py`](../src/nexo_sdk.py).

It is intentionally small. The goal is to expose the public mental model, not every MCP tool:

- `remember(...)`
- `recall(...)`
- `consolidate(...)`
- `run_workflow(...)`

## Example

```python
from nexo_sdk import NEXOClient

client = NEXOClient()

client.remember(
    "Release tasks must close with evidence.",
    title="Release evidence rule",
    domain="nexo",
)

hits = client.recall("release evidence", days=30)

client.consolidate(max_insights=8)

run = client.run_workflow(
    "SID-123",
    "Prepare v3.0 release",
    steps=[
        {"step_key": "doctor", "title": "Run doctor"},
        {"step_key": "package", "title": "Package release"},
    ],
)
```

## Notes

- The SDK is a thin wrapper over `nexo call ... --json-output`.
- It works best on the same machine where the NEXO runtime is installed.
- For low-level operations, keep using the full MCP tool surface.
