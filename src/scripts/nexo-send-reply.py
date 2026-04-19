#!/usr/bin/env python3
# nexo: doctor_allow_db=true
"""
NEXO Send Reply — Envía respuestas email via SMTP (Mundiserver).
Mantiene hilos con In-Reply-To y References.

Uso:
  python3 nexo-send-reply.py \
    --to "Name <email>" \
    --cc "Name <email>" \
    --subject "Re: Subject" \
    --in-reply-to "<message-id>" \
    --references "<ref1> <ref2>" \
    --body-file /tmp/nexo-reply.txt \
    [--html-file /tmp/nexo-reply.html] \
    [--quote-file /tmp/nexo-quote.txt] \
    [--quote-from "Name <email>"] \
    [--quote-date "date string"] \
    [--attach /ruta/al/archivo] \
    [--attachment /ruta/al/archivo]
"""

import argparse
import html
import imaplib
import json
import mimetypes
import re
import smtplib
import sqlite3
import ssl
import sys
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from paths import nexo_email_dir
from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
EMAIL_BASE_DIR = nexo_email_dir()
CONFIG_PATH = EMAIL_BASE_DIR / "config.json"
EMAIL_DB_PATH = EMAIL_BASE_DIR / "nexo-email.db"
EVENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT NOT NULL,
    event TEXT NOT NULL CHECK(event IN ('opened','processing','ack','replied','commitment','action_done','resolution','debt_flagged')),
    timestamp TEXT DEFAULT (datetime('now','localtime')),
    detail TEXT,
    meta TEXT,
    FOREIGN KEY (email_id) REFERENCES emails(message_id)
);
CREATE INDEX IF NOT EXISTS idx_ee_email ON email_events(email_id);
CREATE INDEX IF NOT EXISTS idx_ee_event ON email_events(event);
CREATE INDEX IF NOT EXISTS idx_ee_ts ON email_events(timestamp);
"""

ACK_PATTERNS = (
    r"\bme pongo ya\b",
    r"\bahora mismo\b",
    r"\bvoy a revisarl[oa]\b",
    r"\blo reviso ahora\b",
    r"\bvoy a mirarl[oa]\b",
)
COMMITMENT_PATTERNS = (
    r"\bte aviso cuando\b",
    r"\best[aá] en desarrollo\b",
    r"\bte ir[eé] avisando\b",
    r"\ben cuanto est[eé]\b",
    r"\bte confirmo cuando\b",
)
RESOLUTION_PATTERNS = (
    r"\brespondo a tus preguntas\b",
    r"\bte respondo punto por punto\b",
    r"\bte detallo\b",
    r"\blisto\b",
    r"\baqu[ií] tienes\b",
    r"\bimplementad[oa]\b",
    r"\bya funciona\b",
    r"\badjunto el\b",
    r"\bhecho\b",
    r"\bya est[aá]\b",
)


def load_config(label: str | None = None):
    """Plan F1 — prefer email_accounts table over the legacy JSON.

    Mirrors nexo-email-monitor.py::load_config. Falls back to the
    legacy JSON if the table is empty (pre-migration operator).
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _src = str(_Path(__file__).resolve().parents[1])
        if _src not in _sys.path:
            _sys.path.insert(0, _src)
        from email_config import load_email_config  # type: ignore
        cfg = load_email_config(label=label)
        if cfg:
            return cfg
    except Exception:
        pass
    with open(CONFIG_PATH) as f:
        return json.load(f)


def ensure_email_events_table(conn):
    conn.executescript(EVENT_TABLE_SQL)


def normalize_reply_text(text):
    return re.sub(r"\s+", " ", (text or "").strip()).strip()


def _assistant_display_name(default: str = "Nova") -> str:
    try:
        from automation_controls import get_operator_profile

        profile = get_operator_profile()
    except Exception:
        profile = {}
    value = str((profile or {}).get("assistant_name") or "").strip()
    return value or default


def _signature_label(config: dict) -> str:
    assistant_name = _assistant_display_name()
    sender = str((config or {}).get("email") or "").strip()
    return f"{assistant_name} — {sender}" if sender else assistant_name


def _message_id_domain(config: dict) -> str:
    sender = str((config or {}).get("email") or "").strip()
    if "@" in sender:
        domain = sender.rsplit("@", 1)[-1].strip().strip(">")
        if domain:
            return domain
    return "localhost"


def classify_reply_event(body_text):
    normalized = normalize_reply_text(body_text).lower()
    if not normalized:
        return "replied"
    if any(re.search(pattern, normalized) for pattern in RESOLUTION_PATTERNS):
        return "resolution"
    if any(re.search(pattern, normalized) for pattern in ACK_PATTERNS):
        return "ack"
    if any(re.search(pattern, normalized) for pattern in COMMITMENT_PATTERNS):
        return "commitment"
    return "replied"


def _existing_email(conn, message_id):
    if not message_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM emails WHERE message_id = ? LIMIT 1",
        (message_id,),
    ).fetchone()
    return bool(row)


def resolve_tracked_email_id(conn, in_reply_to, references):
    if _existing_email(conn, in_reply_to):
        return in_reply_to
    if not references:
        return ""
    candidates = re.findall(r"<[^>]+>", references)
    for candidate in reversed(candidates):
        if _existing_email(conn, candidate):
            return candidate
    return ""


def _has_open_action(conn, email_id):
    row = conn.execute(
        """
        SELECT
            MAX(CASE WHEN event IN ('ack', 'commitment') THEN timestamp END),
            MAX(CASE WHEN event = 'action_done' THEN timestamp END)
        FROM email_events
        WHERE email_id = ?
        """,
        (email_id,),
    ).fetchone()
    if not row or not row[0]:
        return False
    latest_open, latest_done = row
    return not latest_done or latest_done < latest_open


def record_reply_lifecycle(in_reply_to, references, body_text, *, subject="", to="", cc="", message_id="", db_path=EMAIL_DB_PATH):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ensure_email_events_table(conn)
        tracked_email_id = resolve_tracked_email_id(conn, in_reply_to, references)
        if not tracked_email_id:
            conn.close()
            return None

        event = classify_reply_event(body_text)
        detail = normalize_reply_text(body_text)[:200]
        meta = json.dumps(
            {
                "subject": subject,
                "to": to,
                "cc": cc,
                "reply_message_id": message_id,
                "in_reply_to": in_reply_to,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        conn.execute(
            "INSERT INTO email_events (email_id, event, detail, meta) VALUES (?, ?, ?, ?)",
            (tracked_email_id, event, detail, meta),
        )
        if event == "resolution" and _has_open_action(conn, tracked_email_id):
            conn.execute(
                "INSERT INTO email_events (email_id, event, detail, meta) VALUES (?, 'action_done', ?, ?)",
                (
                    tracked_email_id,
                    "Auto-closed by resolution reply.",
                    meta,
                ),
            )
        conn.commit()
        conn.close()
        return event
    except Exception as exc:
        print(f"WARN: email lifecycle tracking failed: {exc}", file=sys.stderr)
        return None


def build_quoted_text(quote_file, quote_from, quote_date):
    """Build quoted text block for reply."""
    if not quote_file or not Path(quote_file).exists():
        return ""

    quote_body = Path(quote_file).read_text(encoding="utf-8").strip()
    quoted_lines = "\n".join(f"> {line}" for line in quote_body.split("\n"))

    header = ""
    if quote_from and quote_date:
        header = f"\nEl {quote_date}, {quote_from} escribió:\n\n"
    elif quote_from:
        header = f"\n{quote_from} escribió:\n\n"

    return f"{header}{quoted_lines}"


def build_thread_text(thread_file):
    """Build full thread history block from a file containing all previous messages."""
    if not thread_file or not Path(thread_file).exists():
        return ""
    thread_body = Path(thread_file).read_text(encoding="utf-8").strip()
    if not thread_body:
        return ""
    return f"\n\n{'─' * 40}\n{thread_body}"


def build_html_thread(thread_file):
    """Build full thread history as HTML."""
    if not thread_file or not Path(thread_file).exists():
        return ""
    thread_body = Path(thread_file).read_text(encoding="utf-8").strip()
    if not thread_body:
        return ""
    import html as html_mod
    escaped = html_mod.escape(thread_body)
    return f"""
<hr style="border:none;border-top:1px solid #ccc;margin:20px 0;">
<div style="color:#555;font-size:13px;">
<pre style="white-space:pre-wrap;font-family:inherit;margin:0;">{escaped}</pre>
</div>"""


def build_html_quoted(quote_file, quote_from, quote_date):
    """Build quoted HTML block."""
    if not quote_file or not Path(quote_file).exists():
        return ""

    quote_body = Path(quote_file).read_text(encoding="utf-8").strip()
    import html as html_mod
    escaped = html_mod.escape(quote_body)

    header = ""
    if quote_from and quote_date:
        header = f"<p>El {quote_date}, {html_mod.escape(quote_from)} escribió:</p>"

    return f"""
{header}
<blockquote style="margin:10px 0;padding:10px 15px;border-left:3px solid #ccc;color:#555;">
<pre style="white-space:pre-wrap;font-family:inherit;margin:0;">{escaped}</pre>
</blockquote>"""


def send_email(config, to, cc, subject, body_text, body_html, in_reply_to, references, attachments=None):
    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((_assistant_display_name(), config["email"]))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=_message_id_domain(config))
    msg["X-Mailer"] = "NEXO/2.0"

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    # Text/HTML body as alternative part
    body_part = MIMEMultipart("alternative")
    body_part.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        body_part.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(body_part)

    # Attachments
    for filepath in (attachments or []):
        p = Path(filepath)
        if not p.exists():
            print(f"WARN: attachment not found: {filepath}", file=sys.stderr)
            continue
        ctype, encoding = mimetypes.guess_type(str(p))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(p, "rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=p.name)
        msg.attach(part)

    # Collect all recipients
    recipients = [addr.strip() for addr in to.split(",")]
    if cc:
        recipients += [addr.strip() for addr in cc.split(",")]
    # Extract email addresses from "Name <email>" format
    clean_recipients = []
    for r in recipients:
        if "<" in r and ">" in r:
            clean_recipients.append(r[r.index("<")+1:r.index(">")])
        else:
            clean_recipients.append(r.strip())

    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=context)
    server.login(config["email"], config["password"])
    server.sendmail(config["email"], clean_recipients, msg.as_string())
    server.quit()

    return msg["Message-ID"], msg.as_bytes()


def save_to_sent(config, raw_message: bytes, folder: str = ""):
    """Persist a sent message in IMAP Sent so thread reconstruction can see it later."""
    imap_host = str((config or {}).get("imap_host") or "").strip()
    if not imap_host or not str((config or {}).get("password") or ""):
        return False
    resolved_folder = str(folder or config.get("sent_folder") or "INBOX.Sent").strip() or "INBOX.Sent"
    client = imaplib.IMAP4_SSL(imap_host, int(config.get("imap_port") or 993))
    try:
        client.login(config["email"], config["password"])
        client.append(resolved_folder, "\\Seen", None, raw_message)
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return True


def build_parser():
    """Create the CLI parser for reply sending."""
    parser = argparse.ArgumentParser(description="NEXO Send Reply")
    parser.add_argument("--to", required=True)
    parser.add_argument("--cc", default="")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--account-label", default="", help="Email account label to send from")
    parser.add_argument("--in-reply-to", default="")
    parser.add_argument("--references", default="")
    parser.add_argument("--body-file", required=True, help="Plain text body file")
    parser.add_argument("--html-file", default="", help="HTML body file (optional)")
    parser.add_argument("--quote-file", default="")
    parser.add_argument("--quote-from", default="")
    parser.add_argument("--quote-date", default="")
    parser.add_argument("--thread-file", default="", help="Full thread history file (all previous messages)")
    parser.add_argument("--attach", "--attachment", dest="attach", action="append", default=[], help="File to attach (can repeat)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    config = load_config(label=(args.account_label or "").strip() or None)

    # Read body
    reply_body = Path(args.body_file).read_text(encoding="utf-8").strip()
    body_text = reply_body

    # Build quoted text (immediate parent) + full thread history
    quoted = build_quoted_text(args.quote_file, args.quote_from, args.quote_date)
    thread = build_thread_text(args.thread_file)
    if quoted:
        body_text = f"{body_text}\n\n{quoted}"
    if thread:
        body_text = f"{body_text}{thread}"

    # HTML body
    body_html = None
    html_thread = build_html_thread(args.thread_file)
    if args.html_file and Path(args.html_file).exists():
        html_content = Path(args.html_file).read_text(encoding="utf-8").strip()
        html_quote = build_html_quoted(args.quote_file, args.quote_from, args.quote_date)
        body_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#333;line-height:1.6;">
{html_content}
{html_quote}
{html_thread}
</body></html>"""
    else:
        # Auto-generate HTML from plain text
        escaped_body = html.escape(Path(args.body_file).read_text(encoding="utf-8").strip())
        paragraphs = escaped_body.split("\n\n")
        html_body = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)
        html_quote = build_html_quoted(args.quote_file, args.quote_from, args.quote_date)
        signature = html.escape(_signature_label(config))
        body_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#333;line-height:1.6;">
{html_body}
{html_quote}
{html_thread}
<hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
<p style="color:#888;font-size:11px;">{signature}</p>
</body></html>"""

    try:
        msg_id, raw_message = send_email(
            config, args.to, args.cc, args.subject,
            body_text, body_html,
            args.in_reply_to, args.references,
            attachments=args.attach
        )
        try:
            save_to_sent(config, raw_message)
        except Exception as sent_exc:
            print(f"WARN: sent copy not saved to IMAP Sent: {sent_exc}", file=sys.stderr)
        record_reply_lifecycle(
            args.in_reply_to,
            args.references,
            reply_body,
            subject=args.subject,
            to=args.to,
            cc=args.cc,
            message_id=msg_id,
        )
        print(f"OK:{msg_id}")
    except Exception as e:
        print(f"FAIL:{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
