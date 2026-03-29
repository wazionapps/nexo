"""Menu generator — NEXO operations center."""

from datetime import datetime, timedelta
import json
import subprocess
import sys
from pathlib import Path
from tools_sessions import handle_status
from tools_reminders import handle_reminders
from db import get_db


def _get_date_str() -> str:
    """Get formatted date in Madrid timezone."""
    try:
        result = subprocess.run(
            ["date", "+%A %d de %B de %Y, %H:%M"],
            capture_output=True, text=True,
            env={"TZ": "Europe/Madrid", "PATH": "/usr/bin:/bin", "LANG": "es_ES.UTF-8"}
        )
        return result.stdout.strip()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


MENU_ITEMS = [
    ("Proyectos", [
        ("1", "WAzion - Revisar estado del proyecto"),
        ("9", "Claude Agent VPS - Revisar cambios autonomos"),
    ]),
    ("Publicidad", [
        ("7", "Google Ads - Administrar campanas (Recambios + WAzion)"),
        ("7b", "Meta Ads - Administrar campanas Facebook/Instagram"),
        ("7c", "Ads Tracking - Revision combinada Google+Meta"),
    ]),
    ("Shopify", [
        ("4", "Shopify Theme Sync - Sincronizar tema"),
        ("5", "Shopify Scripts - Ejecutar scripts periodicos"),
        ("6", "Cambiar Promocion Shopify"),
    ]),
    ("Servidor e Infraestructura", [
        ("2", "Servidor - Chequeo your-server.example.com"),
        ("3", "WhatsApp Logs - Revisar logs your-whatsapp-account"),
        ("11", "File Tracker - Reporte archivos PHP"),
        ("12", "Google Cloud - Gasto, consumo y estado GCP"),
    ]),
    ("Comunicacion y Monitorizacion", [
        ("8", "Recovery Optimizer - Analisis IA semanal (LUNES)"),
        ("10", "Recovery Monitor - Estado emails/WA recovery (24h)"),
        ("13", "Review Monitor - Estado emails/WA resenas"),
        ("14", "WhatsApp Analisis Completo - Estadisticas globales"),
        ("15", "Google Analytics - Revisar analiticas web"),
        ("16", "Email Review - Revisar bandejas y spam"),
    ]),
    ("Informes y SEO", [
        ("17", "Auditoria Search Console (cada 2 semanas)"),
        ("18", "Re-envio sitemaps (cada 30 dias)"),
        ("19", "Verificacion SEO metas"),
        ("20", "Informe Email Semanal (domingos)"),
    ]),
]


def _get_dashboard_alerts() -> list[dict]:
    """Run proactive dashboard and return alerts."""
    try:
        script = Path.home() / "claude" / "scripts" / "nexo-proactive-dashboard.py"
        if not script.exists():
            return []
        result = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return []


def _get_memory_review_summary() -> dict:
    """Return counts of due memory reviews."""
    try:
        conn = get_db()
        now_epoch = datetime.now().timestamp()
        now_iso = datetime.now().isoformat(timespec="seconds")
        due_learnings = conn.execute(
            "SELECT COUNT(*) FROM learnings WHERE review_due_at IS NOT NULL AND status != 'superseded' AND review_due_at <= ?",
            (now_epoch,)
        ).fetchone()[0]
        due_decisions = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE review_due_at IS NOT NULL AND status != 'reviewed' AND review_due_at <= ?",
            (now_iso,)
        ).fetchone()[0]
        return {
            "learnings": due_learnings,
            "decisions": due_decisions,
            "total": due_learnings + due_decisions,
        }
    except Exception:
        return {"learnings": 0, "decisions": 0, "total": 0}


def handle_menu() -> str:
    """Generate the full operations menu with alerts."""
    date_str = _get_date_str()
    W = 56  # inner width

    lines = []
    lines.append("╔" + "═" * W + "╗")
    lines.append("║" + "NEXO — CENTRO DE OPERACIONES".center(W) + "║")
    lines.append("║" + date_str.center(W) + "║")
    lines.append("╠" + "═" * W + "╣")

    # Proactive dashboard alerts
    dashboard_alerts = _get_dashboard_alerts()
    memory_reviews = _get_memory_review_summary()
    due = handle_reminders("due")
    has_alerts = dashboard_alerts or memory_reviews["total"] > 0 or (due and "Sin recordatorios" not in due)

    if has_alerts:
        lines.append("║" + "  ALERTAS PROACTIVAS".ljust(W) + "║")
        lines.append("╠" + "═" * W + "╣")

        if dashboard_alerts:
            for alert in dashboard_alerts[:10]:  # Top 10
                sev = alert.get("severity", "low")
                icon = {"high": "!!!", "medium": " ! ", "low": " . "}.get(sev, " . ")
                text = alert.get("title", "")[:W - 8]
                lines.append("║" + f"  {icon} {text}".ljust(W) + "║")
            if len(dashboard_alerts) > 10:
                more = len(dashboard_alerts) - 10
                lines.append("║" + f"  ... y {more} alertas mas".ljust(W) + "║")

        if memory_reviews["total"] > 0:
            text = (
                f"MEMORIA: {memory_reviews['total']} revisiones pendientes "
                f"({memory_reviews['decisions']} decisiones, {memory_reviews['learnings']} learnings)"
            )[:W - 4]
            lines.append("║" + f"  !  {text}".ljust(W) + "║")

        if due and "Sin recordatorios" not in due:
            for reminder_line in due.split("\n"):
                if reminder_line.strip():
                    truncated = reminder_line[:W - 2]
                    lines.append("║" + f"  {truncated}".ljust(W) + "║")

        lines.append("╠" + "═" * W + "╣")

    # Menu categories
    for category, items in MENU_ITEMS:
        lines.append("║" + f"  {category.upper()}".ljust(W) + "║")
        lines.append("║" + "─" * W + "║")
        for num, desc in items:
            entry = f"  {num:>3}. {desc}"
            lines.append("║" + entry.ljust(W) + "║")
        lines.append("╠" + "═" * W + "╣")

    # Backlog: ideas, proyectos futuros, tareas sin fecha o lejanas
    try:
        conn = get_db()
        cutoff = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        # Reminders sin fecha (backlog/ideas)
        no_date = conn.execute(
            "SELECT id, description, category FROM reminders WHERE status LIKE 'PENDIENTE%' AND (date IS NULL OR date='') ORDER BY category, id"
        ).fetchall()
        # Reminders con fecha > 7 días (futuro)
        future = conn.execute(
            "SELECT id, description, date, category FROM reminders WHERE status LIKE 'PENDIENTE%' AND date > ? ORDER BY date",
            (cutoff,)
        ).fetchall()
        # Followups sin fecha
        nf_no_date = conn.execute(
            "SELECT id, description FROM followups WHERE status NOT LIKE 'COMPLETADO%' AND (date IS NULL OR date='') ORDER BY id"
        ).fetchall()

        if no_date or future or nf_no_date:
            lines.append("║" + "  BACKLOG / IDEAS / FUTURO".ljust(W) + "║")
            lines.append("║" + "─" * W + "║")

            if no_date:
                by_cat = {}
                for r in no_date:
                    cat = (r["category"] or "general").capitalize()
                    by_cat.setdefault(cat, []).append(r)
                for cat, items in by_cat.items():
                    lines.append("║" + f"  [{cat}]".ljust(W) + "║")
                    for r in items:
                        short = r["description"][:W - 10]
                        lines.append("║" + f"    {r['id']}: {short}".ljust(W) + "║")

            if future:
                lines.append("║" + f"  [Programado]".ljust(W) + "║")
                for r in future:
                    short = r["description"][:W - 18]
                    lines.append("║" + f"    {r['id']} ({r['date']}): {short}".ljust(W) + "║")

            if nf_no_date:
                lines.append("║" + f"  [Followups pendientes]".ljust(W) + "║")
                for r in nf_no_date:
                    short = r["description"][:W - 12]
                    lines.append("║" + f"    {r['id']}: {short}".ljust(W) + "║")

            lines.append("╠" + "═" * W + "╣")
    except Exception as e:
        lines.append("║" + f"  ⚠ Error backlog: {e}".ljust(W) + "║")
        lines.append("╠" + "═" * W + "╣")

    # Active sessions
    sessions = handle_status()
    if "Sin sesiones" not in sessions:
        lines.append("║" + "  SESIONES ACTIVAS".ljust(W) + "║")
        lines.append("║" + "─" * W + "║")
        for s_line in sessions.split("\n"):
            if s_line.strip() and "SESIONES ACTIVAS" not in s_line:
                truncated = s_line[:W - 2]
                lines.append("║" + f"  {truncated}".ljust(W) + "║")
        lines.append("╠" + "═" * W + "╣")

    # Replace last ╠═╣ with bottom border
    if lines[-1].startswith("╠"):
        lines[-1] = "╚" + "═" * W + "╝"
    else:
        lines.append("╚" + "═" * W + "╝")

    return "\n".join(lines)
