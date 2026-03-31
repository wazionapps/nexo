#!/usr/bin/env python3
"""Quick email sender for NEXO progress updates."""
import smtplib, sys
from email.mime.text import MIMEText
from email.utils import formataddr

def send(subject, body, to="user@example.com", cc="user@example.com"):
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = formataddr(('NEXO', 'nexo@example.com'))
    msg['To'] = to
    msg['Cc'] = cc
    msg['Subject'] = subject
    smtp = smtplib.SMTP_SSL(os.environ.get('NEXO_SMTP_HOST', 'smtp.example.com'), int(os.environ.get('NEXO_SMTP_PORT', '465')))
    smtp.login(FROM_EMAIL, os.environ.get('NEXO_SMTP_PASSWORD', ''))
    recipients = [to]
    if cc:
        recipients.append(cc)
    smtp.send_message(msg)
    smtp.quit()
    print(f"OK — sent to {to}")

if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else "NEXO Update"
    body = sys.argv[2] if len(sys.argv) > 2 else "No body"
    send(subject, body)
