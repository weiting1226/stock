"""寄送搶位結果通知信（SMTP，帳密走環境變數/GitHub Secrets，不進repo）。"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def send_email(to_addr: str, subject: str, body: str, attachment: Path | None = None) -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    port = int(os.environ.get("SMTP_PORT", "587"))

    if not (host and user and password):
        print("[notify] 未設定 SMTP_HOST/SMTP_USER/SMTP_PASS，改成只印出通知內容：")
        print(f"  收件人：{to_addr}\n  主旨：{subject}\n  內容：\n{body}")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(body)

    if attachment and attachment.exists():
        data = attachment.read_bytes()
        maintype, subtype = ("image", "png") if attachment.suffix == ".png" else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment.name)

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"[notify] 已寄出通知信給 {to_addr}")
