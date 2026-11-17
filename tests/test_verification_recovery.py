from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import verification_recovery

from scrapers.source_search_runner import CnkiCookieReplacementRequired
from verification_recovery import (
    MAX_PROBE_DELAY_MINUTES,
    MIN_PROBE_DELAY_MINUTES,
    probe_shard,
    schedule_next_probe,
)


class VerificationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.service_inactive = patch("verification_recovery.service_is_active", return_value=False)
        self.service_inactive.start()
        self.addCleanup(self.service_inactive.stop)

    def test_start_if_ready_starts_only_ready_unblocked_checkpoint(self):
        ready = {"blocked_reason": "", "verification_probe_result": "ready"}
        blocked = {"verification_probe_result": "cookie_required"}
        verification_blocked = {
            "blocked_reason": "verification_required",
            "verification_probe_result": "ready",
        }

        with patch("verification_recovery.systemctl_start") as start, patch(
            "verification_recovery.save_checkpoint"
        ):
            self.assertTrue(verification_recovery.start_if_ready(1, ready))
            self.assertFalse(verification_recovery.start_if_ready(1, blocked))
            self.assertFalse(verification_recovery.start_if_ready(1, verification_blocked))

        start.assert_called_once_with("cnki-scraper3@1.service")

    def test_start_if_ready_does_not_start_active_service(self):
        ready = {"verification_probe_result": "ready"}

        with patch("verification_recovery.service_is_active", return_value=True), patch(
            "verification_recovery.systemctl_start"
        ) as start:
            started = verification_recovery.start_if_ready(1, ready)

        self.assertFalse(started)
        start.assert_not_called()

    def test_start_if_ready_does_not_write_checkpoint_as_privileged_helper(self):
        ready = {"verification_probe_result": "ready"}

        with patch("verification_recovery.service_is_active", return_value=False), patch(
            "verification_recovery.save_checkpoint"
        ) as save, patch("verification_recovery.systemctl_start") as start:
            started = verification_recovery.start_if_ready(1, ready)

        self.assertTrue(started)
        save.assert_not_called()
        start.assert_called_once_with("cnki-scraper3@1.service")

    def test_probe_clears_stale_ready_marker_after_recovery(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "blocked_reason": "",
            "verification_probe_result": "ready",
        }

        with patch("verification_recovery.save_checkpoint") as save:
            result = probe_shard(1, 3, checkpoint, "checkpoint.json", now)

        self.assertEqual(result, "ignored")
        self.assertEqual(save.call_args.args[1]["verification_probe_result"], "")

    def test_first_verification_pause_schedules_probe_in_15_minutes(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)

        checkpoint = schedule_next_probe({}, now, verification_still_present=True)

        self.assertEqual(checkpoint["verification_probe_delay_minutes"], MIN_PROBE_DELAY_MINUTES)
        self.assertEqual(
            checkpoint["verification_probe_next_at"],
            (now + timedelta(minutes=15)).isoformat(),
        )

    def test_verification_pause_escalates_to_60_then_120_minutes(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {"verification_probe_delay_minutes": 30}

        checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=True)
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], 60)
        checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=True)
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], MAX_PROBE_DELAY_MINUTES)
        checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=True)
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], MAX_PROBE_DELAY_MINUTES)

    def test_full_backoff_sequence_and_reset(self):
        now = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
        checkpoint = {"current_journal": "journal", "completed_journals": ["done"]}
        for minutes in (15, 30, 60, 120, 120):
            checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=True)
            self.assertEqual(checkpoint["verification_probe_delay_minutes"], minutes)
            self.assertEqual(checkpoint["verification_probe_next_at"], (now + timedelta(minutes=minutes)).isoformat())
            self.assertEqual(checkpoint["completed_journals"], ["done"])
            now = datetime.fromisoformat(checkpoint["verification_probe_next_at"])
        checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=False)
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], 0)
        self.assertEqual(checkpoint["verification_probe_next_at"], "")
        checkpoint = schedule_next_probe(checkpoint, now, verification_still_present=True)
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], 15)

    def test_probe_reports_ready_after_fresh_search_and_detail_success(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        scraper = Mock()
        scraper.search_page.return_value = ([{"detail_url": "https://kns.cnki.net/fresh"}], 1)
        scraper.fetch_detail_page.return_value = "<html><input id='paramkcmslink' value='v=fresh'/><div id='ChDivSummary'>有效详情</div></html>"

        with patch("verification_recovery.SourceSearchScraper", return_value=scraper):
            result = probe_shard(
                shard_id=1,
                shard_count=3,
                checkpoint=checkpoint,
                checkpoint_path="checkpoint.json",
                now=now,
            )

        self.assertEqual(result, "ready")
        scraper.search_page.assert_called_once_with("期刊A", page=1, page_size=50)
        scraper.fetch_detail_page.assert_called_once()

    def test_probe_initializes_legacy_verification_checkpoint_without_network(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
        }

        with patch("verification_recovery.SourceSearchScraper") as scraper_class, patch(
            "verification_recovery.save_checkpoint"
        ) as save:
            result = probe_shard(
                shard_id=1,
                shard_count=3,
                checkpoint=checkpoint,
                checkpoint_path="checkpoint.json",
                now=now,
            )

        self.assertEqual(result, "scheduled")
        scraper_class.assert_not_called()
        saved = save.call_args.args[1]
        self.assertEqual(saved["verification_probe_delay_minutes"], 15)

    def test_probe_does_not_mark_http_200_verification_page_ready(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        scraper = Mock()
        scraper.search_page.return_value = ([{"detail_url": "https://kns.cnki.net/fresh"}], 1)
        scraper.fetch_detail_page.return_value = "<html><title>安全验证</title><body>/verify/home</body></html>"

        with patch("verification_recovery.SourceSearchScraper", return_value=scraper), patch(
            "verification_recovery.save_checkpoint"
        ) as save:
            result = probe_shard(
                shard_id=1,
                shard_count=3,
                checkpoint=checkpoint,
                checkpoint_path="checkpoint.json",
                now=now,
            )

        self.assertEqual(result, "deferred")
        saved = save.call_args.args[1]
        self.assertEqual(saved["blocked_reason"], "verification_required")

    def test_probe_rejects_http_200_without_detail_structure(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        scraper = Mock()
        scraper.search_page.return_value = ([{"detail_url": "https://kns.cnki.net/fresh"}], 1)
        scraper.fetch_detail_page.return_value = "<html><body>generic 200</body></html>"

        with patch("verification_recovery.SourceSearchScraper", return_value=scraper), patch(
            "verification_recovery.save_checkpoint"
        ) as save:
            result = probe_shard(1, 3, checkpoint, "checkpoint.json", now)

        self.assertEqual(result, "deferred")
        self.assertEqual(save.call_args.args[1]["blocked_reason"], "verification_required")

    def test_probe_starts_main_service_only_when_main_is_inactive(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        with patch("verification_recovery.service_is_active", return_value=True), patch(
            "verification_recovery.SourceSearchScraper"
        ) as scraper_class:
            result = probe_shard(1, 3, checkpoint, "checkpoint.json", now)

        self.assertEqual(result, "ignored")
        scraper_class.assert_not_called()

    def test_probe_marks_cookie_replacement_without_starting_service(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        scraper = Mock()
        scraper.search_page.side_effect = CnkiCookieReplacementRequired("人工更新 Cookie")

        with patch("verification_recovery.SourceSearchScraper", return_value=scraper), patch(
            "verification_recovery.send_cookie_replacement_email", return_value=True
        ) as notify, patch("verification_recovery.save_checkpoint") as save:
            result = probe_shard(
                shard_id=1,
                shard_count=3,
                checkpoint=checkpoint,
                checkpoint_path="checkpoint.json",
                now=now,
            )

        self.assertEqual(result, "cookie_required")
        notify.assert_called_once()
        saved = save.call_args.args[1]
        self.assertEqual(saved["blocked_reason"], "cookie_replacement_required")

    def test_probe_keeps_stopped_and_escalates_when_verification_remains(self):
        now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
        checkpoint = {
            "current_journal": "期刊A",
            "blocked_reason": "verification_required",
            "verification_probe_next_at": now.isoformat(),
            "verification_probe_delay_minutes": 30,
        }
        scraper = Mock()
        scraper.search_page.side_effect = RuntimeError("verify redirect")

        with patch("verification_recovery.SourceSearchScraper", return_value=scraper), patch(
            "verification_recovery.systemctl_start"
        ) as start, patch("verification_recovery.save_checkpoint") as save:
            result = probe_shard(
                shard_id=1,
                shard_count=3,
                checkpoint=checkpoint,
                checkpoint_path="checkpoint.json",
                now=now,
            )

        self.assertEqual(result, "deferred")
        start.assert_not_called()
        saved = save.call_args.args[1]
        self.assertEqual(saved["verification_probe_delay_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
