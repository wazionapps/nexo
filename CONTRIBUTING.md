# Contributing to NEXO

Thanks for your interest in contributing to NEXO. This guide will help you get started.

## How to contribute

### Reporting issues

- Use GitHub Issues for bugs, feature requests, and questions
- Include your macOS version, Python version, and Claude Code version
- For bugs, include steps to reproduce and relevant log output from `~/.nexo/logs/`

### Pull requests

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Run tests: `python -m pytest tests/`
5. Submit a PR with a clear description

### Code style

- Python: Follow PEP 8, use type hints
- Keep functions focused and small
- Add docstrings to all public functions
- Error handling: fail gracefully, never crash the MCP server

### Plugin development

NEXO's plugin system is the best way to extend functionality without modifying core:

```python
# ~/.nexo/plugins/my_plugin.py

def handle_my_tool(param: str) -> str:
    """Description shown to Claude Code."""
    return f"Result: {param}"

TOOLS = [
    (handle_my_tool, "nexo_my_tool", "Short description for the tool catalog"),
]
```

Plugins are auto-loaded at server startup. Use `nexo_plugin_load` to hot-reload during development.

### What we look for in PRs

- Does it solve a real problem?
- Is it tested?
- Does it follow existing patterns?
- Is it documented?
- Does the pre-commit hook pass? (no private data leaks)

## Architecture overview

```
src/
  server.py          — MCP server (FastMCP)
  db.py              — SQLite layer (all tables + CRUD)
  cognitive.py       — Vector engine (embed, search, decay, consolidate)
  plugin_loader.py   — Hot-reload plugin system
  tools_*.py         — Core MCP tool handlers
  plugins/           — Auto-loaded plugins
  scripts/           — Automated processes (decay, audit, postmortem)
```

### Key design principles

1. **SQLite for everything** — No external databases. Everything in `~/.nexo/`
2. **Plugins over core changes** — If it can be a plugin, make it a plugin
3. **Graceful degradation** — If cognitive engine fails, fall back to keyword search
4. **Never block** — Cognitive operations are best-effort, never fail the main tool
5. **Privacy first** — User data stays local. No telemetry. No cloud.

## Release process

Maintainers handle releases. Version bumps follow SemVer:
- **Patch** (0.1.x): Bug fixes, small improvements
- **Minor** (0.x.0): New features, new tools, new plugins
- **Major** (x.0.0): Breaking changes to tool signatures or DB schema

## Code of Conduct

Be respectful. Be constructive. Focus on the code, not the person.
