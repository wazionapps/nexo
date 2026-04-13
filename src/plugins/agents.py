"""Agents plugin — registry of known agent types with their configs."""
from db import create_agent, get_agent, list_agents, update_agent, delete_agent

try:
    from client_preferences import resolve_user_model
    _DEFAULT_AGENT_MODEL = resolve_user_model()
except Exception:
    _DEFAULT_AGENT_MODEL = ""

def handle_agent_get(id: str) -> str:
    """Get an agent's full profile by ID."""
    a = get_agent(id)
    if not a: return f"Agent '{id}' not found."
    lines = [f"AGENT: {a['name']} ({a['id']})", f"  Specialization: {a['specialization']}", f"  Model: {a['model']}"]
    if a["tools"]: lines.append(f"  Tools: {a['tools']}")
    if a["context_files"]: lines.append(f"  Contexto: {a['context_files']}")
    if a["rules"]: lines.append(f"  Reglas: {a['rules']}")
    return "\n".join(lines)

def handle_agent_create(id: str, name: str, specialization: str, model: str = "",
                        tools: str = "", context_files: str = "", rules: str = "") -> str:
    """Register a new agent in the registry."""
    effective_model = model or _DEFAULT_AGENT_MODEL
    create_agent(id, name, specialization, effective_model, tools, context_files, rules)
    return f"Agent '{id}' ({name}) registered. Model: {effective_model}"

def handle_agent_update(id: str, name: str = "", specialization: str = "", model: str = "",
                        tools: str = "", context_files: str = "", rules: str = "") -> str:
    """Update agent fields. Only non-empty fields are changed."""
    kwargs = {}
    for k, v in [("name", name), ("specialization", specialization), ("model", model),
                  ("tools", tools), ("context_files", context_files), ("rules", rules)]:
        if v: kwargs[k] = v
    if not kwargs: return "Nothing to update."
    update_agent(id, **kwargs)
    return f"Agent '{id}' updated."

def handle_agent_list() -> str:
    """List all registered agents."""
    agents = list_agents()
    if not agents: return "No agents registered."
    lines = ["REGISTERED AGENTS:"]
    for a in agents:
        lines.append(f"  {a['id']} — {a['name']} ({a['model']}) — {a['specialization'][:60]}")
    return "\n".join(lines)

def handle_agent_delete(id: str) -> str:
    """Remove an agent from the registry."""
    if not delete_agent(id):
        return f"ERROR: Agent '{id}' not found."
    return f"Agent '{id}' deleted."

TOOLS = [
    (handle_agent_get, "nexo_agent_get", "Get an agent's full profile"),
    (handle_agent_create, "nexo_agent_create", "Register a new agent"),
    (handle_agent_update, "nexo_agent_update", "Update agent fields"),
    (handle_agent_list, "nexo_agent_list", "List all registered agents"),
    (handle_agent_delete, "nexo_agent_delete", "Remove an agent from registry"),
]
