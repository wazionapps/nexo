# Skill Template

Create a directory under one of:
- `NEXO_HOME/skills/<slug>/`
- `src/skills/<slug>/`
- `community/skills/<slug>/`

Required files:
- `skill.json`
- `guide.md`

Optional:
- `script.py` or `script.sh`

Example `skill.json`:

```json
{
  "id": "SK-EXAMPLE",
  "name": "Example Skill",
  "description": "What this skill does.",
  "level": "draft",
  "mode": "guide",
  "source_kind": "personal",
  "execution_level": "none",
  "approval_required": false,
  "tags": ["example"],
  "trigger_patterns": ["example task"],
  "params_schema": {},
  "command_template": {},
  "stable_after_uses": 10
}
```
