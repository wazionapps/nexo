"""Agents plugin — registry of known agent types with their configs."""
from db import create_agent, get_agent, list_agents, update_agent, delete_agent

def handle_agent_get(id: str) -> str:
    """Get an agent's full profile by ID."""
    a = get_agent(id)
    if not a: return f"Agente '{id}' no encontrado."
    lines = [f"AGENTE: {a['name']} ({a['id']})", f"  Especialización: {a['specialization']}", f"  Modelo: {a['model']}"]
    if a["tools"]: lines.append(f"  Tools: {a['tools']}")
    if a["context_files"]: lines.append(f"  Contexto: {a['context_files']}")
    if a["rules"]: lines.append(f"  Reglas: {a['rules']}")
    return "\n".join(lines)

def handle_agent_create(id: str, name: str, specialization: str, model: str = "sonnet",
                        tools: str = "", context_files: str = "", rules: str = "") -> str:
    """Register a new agent in the registry."""
    create_agent(id, name, specialization, model, tools, context_files, rules)
    return f"Agente '{id}' ({name}) registrado. Modelo: {model}"

def handle_agent_update(id: str, name: str = "", specialization: str = "", model: str = "",
                        tools: str = "", context_files: str = "", rules: str = "") -> str:
    """Update agent fields. Only non-empty fields are changed."""
    kwargs = {}
    for k, v in [("name", name), ("specialization", specialization), ("model", model),
                  ("tools", tools), ("context_files", context_files), ("rules", rules)]:
        if v: kwargs[k] = v
    if not kwargs: return "Nada que actualizar."
    update_agent(id, **kwargs)
    return f"Agente '{id}' actualizado."

def handle_agent_list() -> str:
    """List all registered agents."""
    agents = list_agents()
    if not agents: return "Sin agentes registrados."
    lines = ["AGENTES REGISTRADOS:"]
    for a in agents:
        lines.append(f"  {a['id']} — {a['name']} ({a['model']}) — {a['specialization'][:60]}")
    return "\n".join(lines)

def handle_agent_delete(id: str) -> str:
    """Remove an agent from the registry."""
    if not delete_agent(id):
        return f"ERROR: Agente '{id}' no encontrado."
    return f"Agente '{id}' eliminado."

TOOLS = [
    (handle_agent_get, "nexo_agent_get", "Get an agent's full profile"),
    (handle_agent_create, "nexo_agent_create", "Register a new agent"),
    (handle_agent_update, "nexo_agent_update", "Update agent fields"),
    (handle_agent_list, "nexo_agent_list", "List all registered agents"),
    (handle_agent_delete, "nexo_agent_delete", "Remove an agent from registry"),
]
