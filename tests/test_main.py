import sys
import unittest
from unittest.mock import Mock, patch

from main import main, parse_args


class MainTests(unittest.TestCase):
    def test_parse_args_defaults_to_full_relation_pagination(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_args()

        self.assertEqual(args.max_relation_pages, 0)

    def test_parse_args_enables_resume_by_default(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_args()

        self.assertTrue(args.resume)

    def test_parse_args_can_disable_resume(self):
        with patch.object(sys, "argv", ["main.py", "--no-resume"]):
            args = parse_args()

        self.assertFalse(args.resume)

    def test_parse_args_exposes_long_run_request_options(self):
        with patch.object(
            sys,
            "argv",
            [
                "main.py",
                "--delay-min",
                "3",
                "--delay-max",
                "4",
                "--max-retries",
                "5",
                "--backoff-factor",
                "6",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.delay_min, 3)
        self.assertEqual(args.delay_max, 4)
        self.assertEqual(args.max_retries, 5)
        self.assertEqual(args.backoff_factor, 6)

    def test_parse_args_uses_balanced_delay_defaults(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_args()

        self.assertEqual(args.delay_min, 5.0)
        self.assertEqual(args.delay_max, 8.0)
        self.assertEqual(args.backoff_factor, 12.0)

    def test_parse_args_uses_2016_as_default_auto_detect_floor(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_args()

        self.assertEqual(args.year_start_floor, 2016)

    def test_parse_args_can_override_auto_detect_floor(self):
        with patch.object(sys, "argv", ["main.py", "--year-start-floor", "2020"]):
            args = parse_args()

        self.assertEqual(args.year_start_floor, 2020)

    def test_parse_args_defaults_to_three_shards(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_args()

        self.assertEqual(args.shard_count, 3)

    def test_parse_args_exposes_shard_options(self):
        with patch.object(
            sys,
            "argv",
            ["main.py", "--shard-id", "2", "--shard-count", "3", "--shard-output-root", "output-test", "--print-shards"],
        ):
            args = parse_args()

        self.assertEqual(args.shard_id, 2)
        self.assertEqual(args.shard_count, 3)
        self.assertEqual(args.shard_output_root, "output-test")
        self.assertTrue(args.print_shards)

    def test_parse_args_exposes_independent_shard_storage_roots(self):
        with patch.object(
            sys,
            "argv",
            ["main.py", "--shard-cache-root", "cache-3", "--shard-log-root", "logs-3"],
        ):
            args = parse_args()

        self.assertEqual(args.shard_cache_root, "cache-3")
        self.assertEqual(args.shard_log_root, "logs-3")

    def test_main_passes_long_run_request_options_to_scraper(self):
        scraper = Mock()
        scraper.search_journals.return_value = []
        scraper.enrich_articles.return_value = []

        with patch.object(sys, "argv", ["main.py", "--journal", "测试期刊", "--delay-min", "3", "--delay-max", "4", "--max-retries", "5", "--backoff-factor", "6"]), patch(
            "main.SourceSearchScraper", return_value=scraper
        ) as scraper_class:
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(scraper_class.call_args.kwargs["delay_min"], 3)
        self.assertEqual(scraper_class.call_args.kwargs["delay_max"], 4)
        self.assertEqual(scraper_class.call_args.kwargs["max_retries"], 5)
        self.assertEqual(scraper_class.call_args.kwargs["backoff_factor"], 6)
        self.assertTrue(scraper.run.call_args.kwargs["resume"])
        self.assertFalse(scraper.run.call_args.kwargs["collect_results"])

    def test_main_returns_cookie_replacement_exit_code(self):
        scraper = Mock()
        scraper.blocked_reason = "cookie_replacement_required"

        with patch.object(sys, "argv", ["main.py", "--journal", "测试期刊"]), patch(
            "main.SourceSearchScraper", return_value=scraper
        ):
            exit_code = main()

        self.assertEqual(exit_code, 76)

    def test_main_collects_results_when_resume_is_disabled(self):
        scraper = Mock()

        with patch.object(sys, "argv", ["main.py", "--journal", "测试期刊", "--no-resume"]), patch(
            "main.SourceSearchScraper", return_value=scraper
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertFalse(scraper.run.call_args.kwargs["resume"])
        self.assertTrue(scraper.run.call_args.kwargs["collect_results"])

    def test_main_passes_selected_shard_journals_directories_and_default_env_file_to_scraper(self):
        scraper = Mock()

        with patch.object(sys, "argv", ["main.py", "--shard-id", "2", "--shard-count", "3"]), patch(
            "main.load_journal_names", return_value=[f"期刊{index}" for index in range(1, 19)]
        ), patch("main.SourceSearchScraper", return_value=scraper) as scraper_class:
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(scraper.run.call_args.kwargs["journal_names"], ["期刊2", "期刊5", "期刊8", "期刊11", "期刊14", "期刊17"])
        self.assertEqual(scraper_class.call_args.kwargs["output_dir"].name, "shard-2")
        self.assertEqual(scraper_class.call_args.kwargs["cache_dir"].name, "shard-2")
        self.assertEqual(scraper_class.call_args.kwargs["env_path"].name, ".env")

    def test_main_print_shards_does_not_run_scraper(self):
        with patch.object(sys, "argv", ["main.py", "--print-shards", "--shard-count", "3"]), patch(
            "main.load_journal_names", return_value=["期刊1", "期刊2", "期刊3"]
        ), patch("main.SourceSearchScraper") as scraper_class:
            exit_code = main()

        self.assertEqual(exit_code, 0)
        scraper_class.assert_not_called()
    def test_parse_args_exposes_year_slice_options(self):
        with patch.object(sys, "argv", ["main.py", "--slice-by-year", "--year-start", "2023", "--year-end", "2026"]):
            args = parse_args()

        self.assertTrue(args.slice_by_year)
        self.assertEqual(args.year_start, 2023)
        self.assertEqual(args.year_end, 2026)

    def test_main_passes_year_slices_to_scraper(self):
        scraper = Mock()
        scraper.search_journals.return_value = []
        scraper.enrich_articles.return_value = []

        with patch.object(sys, "argv", ["main.py", "--journal", "测试期刊", "--slice-by-year", "--year-start", "2023", "--year-end", "2025"]), patch(
            "main.SourceSearchScraper", return_value=scraper
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(scraper.run.call_args.kwargs["year_slices"], ["2025", "2024", "2023"])


if __name__ == "__main__":
    unittest.main()
