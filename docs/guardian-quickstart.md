# Guardian Quickstart (Fase 2 Protocol Enforcer)

El Guardian es la capa de enforcement runtime que convierte a cualquier cliente Claude (Code, Codex, Desktop) en un agente que obedece el protocolo NEXO al 100%.

## ¿Qué hace el Guardian?

Observa cada mensaje del usuario, cada tool call del agente, y cada fragmento de texto del agente. Cuando detecta:

- **Pre-Edit sin guard_check** → recordatorio de llamar `nexo_guard_check` antes.
- **Correcciones del operador** → exige capturar learning en la ventana siguiente.
- **Declaración prematura de "hecho" sin evidencia** → recordatorio de `nexo_task_close`.
- **Comandos destructivos en hosts read-only** → bloquea.
- **Force-push a main / master / release-*** → bloquea.
- **DELETE/UPDATE SQL sin WHERE contra producción** → bloquea.
- **Otros ~40 patrones** catalogados en `src/presets/guardian_default.json`.

## Instalación

```bash
python3 scripts/install_guardian.py
```

Esto crea / actualiza:

- `~/.nexo/config/guardian.json` — modos por regla (off/shadow/soft/hard).
- `~/.nexo/brain/presets/entities_universal.json` — baseline de entidades.
- `~/.nexo/brain/presets/guardian_default.json` — defaults por versión.
- `~/.nexo/brain/presets/ssh_imported_hosts.json` — hosts de `~/.ssh/config` importados como `access_mode=unknown`.
- `~/.nexo/config/schedule.json` — `automation_backend=claude_code` (si el operador no tiene `automation_user_override=true`).

Flags:

- `--dry-run` — reporta lo que haría sin tocar nada.
- `--force` — sobrescribe guardian.json y presets (destructivo sobre customizaciones).

## Modos por regla

Cada regla puede configurarse en 4 modos:

| Mode | Comportamiento |
|------|----------------|
| `off` | Regla deshabilitada. Core rules (R13/R14/R16/R25/R30) rechazan este valor automáticamente. |
| `shadow` | La regla evalúa y registra, pero no inyecta recordatorios visibles. Útil para rollout. |
| `soft` | Inyecta recordatorio al agente; el agente puede ignorar si el contexto lo justifica. |
| `hard` | Inyecta con prioridad alta; bloqueo efectivo de la acción sin override explícito del operador. |

Edita `~/.nexo/config/guardian.json`:

```json
{
  "version": "1.3.3",
  "rules": {
    "R13_pre_edit_guard": "hard",
    "R25_nora_maria_read_only": "hard",
    "R21_legacy_path": "shadow",
    "R23h_shebang_mismatch": "off"
  }
}
```

Las reglas no listadas en tu archivo heredan el default empaquetado.

## Añadir tus proyectos

El Guardian usa entidades del brain (SQLite + preset) para saber de qué proyectos hablas. Para que R15 (project_context), R19 (require_grep) o R23b (deploy vhost mismatch) funcionen con tus proyectos:

```bash
# Via MCP (preferido):
nexo_entity_create type=project name=MyProject metadata='{"local_path":"/Users/me/work/myproject","aliases":["myproj"]}'

# Para vhosts:
nexo_entity_create type=vhost_mapping name=myshop_com metadata='{"domain":"myshop.com","host":"myserver","docroot":"/var/www/myshop"}'
```

El preset ya viene con 8 vhost_mapping + 8 destructive_command + 3 legacy_path. Añadir los tuyos es puro marginal — el Guardian crece con tu realidad.

## Añadir tus SSH hosts

`install_guardian.py` importa automáticamente tus hosts de `~/.ssh/config`. Quedan como `access_mode=unknown` — si quieres marcar alguno como read-only (Nora/Maria pattern):

```bash
nexo_entity_update name=maria_server type=host metadata='{"access_mode":"read_only","reason":"prod box tenant Maria"}'
```

R25 bloqueará comandos destructivos contra ese host hasta que el operador diga `force OK` en el mensaje.

## Telemetría

Cada inyección del Guardian se registra en `~/.nexo/logs/guardian-telemetry.ndjson`. Datos locales, nunca salen de tu máquina salvo que actives `telemetry_external_optin=true` en el futuro (Fase F).

## Troubleshooting

**"El Guardian no inyecta nada"** — verifica que `~/.nexo/config/guardian.json` existe. Si no, `python3 scripts/install_guardian.py`.

**"Me está bloqueando un comando que sé que quiero ejecutar"** — en el siguiente mensaje al agente, di explícitamente `force OK` o `si borra` (sinónimos permitidos están en `r25_nora_maria_read_only.py::PERMIT_MARKERS`).

**"Quiero apagar una regla concreta"** — edita `~/.nexo/config/guardian.json` → `rules.<rule_id> = "off"`. Core rules no se pueden apagar (defensa en profundidad).
