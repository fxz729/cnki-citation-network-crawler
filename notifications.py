from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def _load_alert_value(env_path: str | Path | None, key: str) -> str:
    environment_value = os.getenv(key, "").strip()
    if environment_value or env_path is None:
        return environment_value
    path = Path(env_path)
    if not path.exists():
        return ""
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def send_cookie_replacement_email(
    shard_id: int,
    reason: str,
    env_path: str | Path | None = None,
) -> bool:
    sender = _load_alert_value(env_path, "CNKI_ALERT_EMAIL_FROM")
    auth_code = _load_alert_value(env_path, "CNKI_ALERT_EMAIL_AUTH_CODE")
    recipient = _load_alert_value(env_path, "CNKI_ALERT_EMAIL_TO")
    if not sender or not auth_code or not recipient:
        return False

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"[CNKI] shared{shard_id} 需要人工更新 Cookie"
    message.set_content(
        f"CNKI shared{shard_id} 在重新加载完整 Cookie 后仍无法通过认证。\n\n"
        f"原因：{reason}\n\n"
        "请重新导出同时包含个人与机构登录态的完整 Cookie，更新服务器 .env 后重启对应 shared。\n"
    )

    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=20) as client:
        client.login(sender, auth_code)
        client.send_message(message)
    return True
