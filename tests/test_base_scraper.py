import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from scrapers.base_scraper import BaseScraper


class BaseScraperTests(unittest.TestCase):
    def test_request_with_retry_retries_throttled_status_with_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = BaseScraper(
                output_dir=Path(temp_dir) / "output",
                cache_dir=Path(temp_dir) / "cache",
                delay_min=1,
                delay_max=2,
                max_retries=2,
                backoff_factor=10,
            )
            throttled = Mock(status_code=429, url="https://example.com/limited")
            ok = Mock(status_code=200, url="https://example.com/ok")

            with patch.object(scraper.session, "request", side_effect=[throttled, ok]) as request, patch(
                "scrapers.base_scraper.time.sleep"
            ) as sleep, patch("scrapers.base_scraper.random.uniform", return_value=1.5):
                response = scraper.request_with_retry("GET", "https://example.com/limited", timeout=5)

        self.assertIs(response, ok)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], 1.5)
        self.assertEqual(sleep.call_args_list[1].args[0], 11.5)

    def test_request_with_retry_retries_request_exceptions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = BaseScraper(
                output_dir=Path(temp_dir) / "output",
                cache_dir=Path(temp_dir) / "cache",
                delay_min=1,
                delay_max=1,
                max_retries=1,
                backoff_factor=10,
            )
            ok = Mock(status_code=200, url="https://example.com/ok")

            with patch.object(scraper.session, "request", side_effect=[requests.Timeout("slow"), ok]) as request, patch(
                "scrapers.base_scraper.time.sleep"
            ) as sleep, patch("scrapers.base_scraper.random.uniform", return_value=1):
                response = scraper.request_with_retry("GET", "https://example.com/slow")

        self.assertIs(response, ok)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_args_list[1].args[0], 11)

    def test_request_with_retry_raises_clear_error_after_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = BaseScraper(
                output_dir=Path(temp_dir) / "output",
                cache_dir=Path(temp_dir) / "cache",
                delay_min=1,
                delay_max=1,
                max_retries=1,
                backoff_factor=10,
            )
            response = Mock(status_code=503, url="https://example.com/down")

            with patch.object(scraper.session, "request", return_value=response), patch(
                "scrapers.base_scraper.time.sleep"
            ), patch("scrapers.base_scraper.random.uniform", return_value=1):
                with self.assertRaisesRegex(ValueError, "503.*https://example.com/down"):
                    scraper.request_with_retry("GET", "https://example.com/down")

    def test_request_with_retry_raises_clear_error_after_request_exception_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = BaseScraper(
                output_dir=Path(temp_dir) / "output",
                cache_dir=Path(temp_dir) / "cache",
                delay_min=1,
                delay_max=1,
                max_retries=1,
                backoff_factor=10,
            )

            with patch.object(scraper.session, "request", side_effect=requests.ConnectionError("network down")), patch(
                "scrapers.base_scraper.time.sleep"
            ), patch("scrapers.base_scraper.random.uniform", return_value=1):
                with self.assertRaisesRegex(ValueError, "network down.*https://example.com/down"):
                    scraper.request_with_retry("GET", "https://example.com/down")


if __name__ == "__main__":
    unittest.main()
