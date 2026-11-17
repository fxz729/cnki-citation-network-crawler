import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notifications import send_cookie_replacement_email


class NotificationTests(unittest.TestCase):
    def test_send_cookie_replacement_email_uses_qq_smtp_ssl(self):
        env = {
            "CNKI_ALERT_EMAIL_FROM": "sender@qq.com",
            "CNKI_ALERT_EMAIL_AUTH_CODE": "smtp-auth-code",
            "CNKI_ALERT_EMAIL_TO": "receiver@qq.com",
        }

        with patch.dict("os.environ", env, clear=True), patch("notifications.smtplib.SMTP_SSL") as smtp_class:
            sent = send_cookie_replacement_email(shard_id=1, reason="刷新 Cookie 后仍收到 401")

        self.assertTrue(sent)
        smtp_class.assert_called_once_with("smtp.qq.com", 465, timeout=20)
        client = smtp_class.return_value.__enter__.return_value
        client.login.assert_called_once_with("sender@qq.com", "smtp-auth-code")
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "receiver@qq.com")
        self.assertIn("shared1", message["Subject"])
        self.assertNotIn("smtp-auth-code", message.as_string())

    def test_send_cookie_replacement_email_reads_project_env_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "CNKI_ALERT_EMAIL_FROM=sender@qq.com\n"
                "CNKI_ALERT_EMAIL_AUTH_CODE=smtp-auth-code\n"
                "CNKI_ALERT_EMAIL_TO=receiver@qq.com\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True), patch("notifications.smtplib.SMTP_SSL") as smtp_class:
                sent = send_cookie_replacement_email(
                    shard_id=1,
                    reason="刷新 Cookie 后仍收到 401",
                    env_path=env_path,
                )

        self.assertTrue(sent)
        client = smtp_class.return_value.__enter__.return_value
        client.login.assert_called_once_with("sender@qq.com", "smtp-auth-code")

    def test_send_cookie_replacement_email_skips_when_not_configured(self):
        with patch.dict("os.environ", {}, clear=True), patch("notifications.smtplib.SMTP_SSL") as smtp_class:
            sent = send_cookie_replacement_email(shard_id=1, reason="刷新 Cookie 后仍收到 401")

        self.assertFalse(sent)
        smtp_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
