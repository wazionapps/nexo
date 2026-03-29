#!/usr/bin/env python3
"""
NEXO Email Reply Helper — Sends email replies with correct threading headers.
NEXO calls this instead of building SMTP manually.

Usage:
  nexo-send-reply.py --to addr --subject "Re: ..." --in-reply-to "<msg-id>" --body "text"
  nexo-send-reply.py --to addr --subject "Re: ..." --in-reply-to "<msg-id>" --body-file /tmp/reply.html --html
  nexo-send-reply.py --to addr --subject "New subject" --body "text"  (new email, no threading)

Options:
  --to          Recipient (required)
  --cc          CC recipients (comma-separated, default: info@example.com)
  --subject     Subject line (required)
  --in-reply-to Message-ID of the email being replied to (for threading)
  --references  Full References chain (optional, defaults to in-reply-to value)
  --body        Plain text body (inline)
  --body-file   Read body from file instead
  --html        Treat body as HTML
  --quote       Original message text to include as quoted reply
  --quote-file  Read original message from file to quote
  --quote-from  Sender of the original message (for "On date, X wrote:")
  --quote-date  Date of the original message
  --attachment  Path to file attachment (can be repeated)
"""

import argparse
import imaplib
import json
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from pathlib import Path
import mimetypes

CONFIG_PATH = Path.home() / ".nexo" / "nexo-email" / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_message(args, config):
    msg = EmailMessage()

    # From / To / CC
    msg["From"] = f"NEXO <{config['email']}>"
    msg["To"] = args.to
    if args.cc:
        msg["Cc"] = args.cc

    # Subject
    msg["Subject"] = args.subject

    # Threading headers — this is the whole point of this script
    if args.in_reply_to:
        msg["In-Reply-To"] = args.in_reply_to
        msg["References"] = args.references or args.in_reply_to

    # Standard headers
    msg["Message-ID"] = make_msgid(domain="example.com")
    msg["Date"] = formatdate(localtime=True)

    # Body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = args.body or ""

    # Quoted original message
    quote_text = ""
    if args.quote_file:
        quote_text = Path(args.quote_file).read_text(encoding="utf-8")
    elif args.quote:
        quote_text = args.quote

    if quote_text:
        quote_from = args.quote_from or args.to
        quote_date = args.quote_date or ""
        attribution = f"El {quote_date}, {quote_from} escribió:" if quote_date else f"{quote_from} escribió:"

        if args.html:
            quoted_html = quote_text.replace("\n", "<br>") if "<" not in quote_text else quote_text
            body = (
                f"{body}<br><br>"
                f"<div style=\"color:#666;\">{attribution}</div>"
                f"<blockquote style=\"margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex;color:#666;\">"
                f"{quoted_html}"
                f"</blockquote>"
            )
        else:
            quoted_lines = "\n".join(f"> {line}" for line in quote_text.splitlines())
            body = f"{body}\n\n{attribution}\n{quoted_lines}"

    if args.html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)

    # Attachments
    if args.attachment:
        for filepath in args.attachment:
            p = Path(filepath)
            if not p.exists():
                print(f"WARNING: attachment not found: {filepath}", file=sys.stderr)
                continue
            mime_type, _ = mimetypes.guess_type(str(p))
            if mime_type is None:
                mime_type = "application/octet-stream"
            maintype, subtype = mime_type.split("/", 1)
            with open(p, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=p.name
                )

    return msg


def send(msg, config):
    with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"]) as server:
        server.login(config["email"], config["password"])
        server.send_message(msg)


def save_to_sent(msg, config):
    """Append the sent message to the IMAP Sent folder."""
    try:
        with imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"]) as imap:
            imap.login(config["email"], config["password"])
            imap.append(
                "INBOX.Sent",
                r"\Seen",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes(),
            )
    except Exception as e:
        print(f"WARNING: could not save to Sent folder: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="NEXO Email Reply Helper")
    parser.add_argument("--to", required=True, help="Recipient email")
    parser.add_argument("--cc", default=None, help="CC recipients (comma-separated, no default)")
    parser.add_argument("--subject", required=True, help="Subject line")
    parser.add_argument("--in-reply-to", default=None, help="Message-ID for threading")
    parser.add_argument("--references", default=None, help="References chain for threading")
    parser.add_argument("--body", default=None, help="Body text inline")
    parser.add_argument("--body-file", default=None, help="Read body from file")
    parser.add_argument("--html", action="store_true", help="Body is HTML")
    parser.add_argument("--quote", default=None, help="Original message text to quote inline")
    parser.add_argument("--quote-file", default=None, help="Read original message from file to quote")
    parser.add_argument("--quote-from", default=None, help="Sender of original (for attribution line)")
    parser.add_argument("--quote-date", default=None, help="Date of original message")
    parser.add_argument("--attachment", action="append", help="File to attach (repeatable)")
    args = parser.parse_args()

    if not args.body and not args.body_file:
        print("ERROR: --body or --body-file required", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    msg = build_message(args, config)
    send(msg, config)
    save_to_sent(msg, config)

    print(f"OK: sent to {args.to} | subject: {args.subject} | threaded: {bool(args.in_reply_to)}")


if __name__ == "__main__":
    main()
