import unittest
from pathlib import Path


class DeploymentServiceTests(unittest.TestCase):
    def test_scraper_service_uses_eight_shards(self):
        service_path = Path(__file__).resolve().parents[1] / "deploy" / "cnki-scraper3@.service"
        service = service_path.read_text(encoding="utf-8")

        self.assertIn("--shard-count 8", service)
        self.assertIn("RestartPreventExitStatus=75 76", service)

    def test_verification_service_uses_eight_shards(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "deploy" / "cnki-verification-recovery@.service").read_text(encoding="utf-8")

        self.assertEqual(service.count("--shard-count 8"), 2)

    def test_cookie_replacement_exit_code_prevents_restart(self):
        service_path = Path(__file__).resolve().parents[1] / "deploy" / "cnki-scraper3@.service"
        service = service_path.read_text(encoding="utf-8")

        self.assertIn("RestartPreventExitStatus=75 76", service)
    def test_verification_recovery_timer_checks_every_minute(self):
        root = Path(__file__).resolve().parents[1]
        timer = (root / "deploy" / "cnki-verification-recovery@.timer").read_text(encoding="utf-8")
        service = (root / "deploy" / "cnki-verification-recovery@.service").read_text(encoding="utf-8")

        self.assertIn("OnUnitActiveSec=1min", timer)
        self.assertIn("verification_recovery.py --shard-id %i", service)
    def test_setup_installs_recovery_units_and_enables_timer(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "deploy" / "setup_shared.sh").read_text(encoding="utf-8")

        self.assertIn("cnki-verification-recovery@.service", script)
        self.assertIn("cnki-verification-recovery@.timer", script)
        self.assertIn("systemctl enable --now", script)

    def test_recovery_service_uses_nonzero_probe_for_non_ready_states(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "deploy" / "cnki-verification-recovery@.service").read_text(encoding="utf-8")

        self.assertIn("Type=oneshot", service)
        self.assertIn("ExecStartPost=+", service)
        self.assertIn("--start-if-ready", service)

    def test_eight_server_provision_makes_service_paths_accessible(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "deploy" / "eight_server_ops.py").read_text(encoding="utf-8")

        self.assertIn('install -d -m 0755 "$TARGET"', script)
        self.assertIn('"$TARGET/output" "$TARGET/cache" "$TARGET/logs"', script)
        self.assertIn('chmod 0600 "$TARGET/.env"', script)
        self.assertIn('-exec chmod a+rX {{}} +', script)

    def test_problematic_journal_uses_exact_search_override(self):
        import json

        root = Path(__file__).resolve().parents[1]
        overrides = json.loads(
            (root / "config" / "journal_overrides.json").read_text(encoding="utf-8")
        )

        self.assertEqual(overrides["管理案例研究与评论"], {"operator": "0"})


if __name__ == "__main__":
    unittest.main()
