from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import requests


LOGGER_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]
RETRY_STATUS_CODES = {403, 429, 500, 502, 503, 504}



def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOGGER_FORMAT))
    logger.addHandler(handler)
    return logger


class BaseScraper:
    def __init__(
        self,
        output_dir: str | Path,
        cache_dir: str | Path,
        delay_min: float = 15.0,
        delay_max: float = 20.0,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 30.0,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.cache_dir = Path(cache_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.logger = setup_logger(self.__class__.__name__)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json, text/html, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )

    def sleep_between_requests(self) -> None:
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def observe_response(self, response: requests.Response, url: str) -> None:
        pass

    def request_with_retry(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_response: requests.Response | None = None
        last_error: requests.RequestException | None = None
        timeout = kwargs.pop("timeout", self.timeout)
        for attempt in range(self.max_retries + 1):
            self.sleep_between_requests()
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                self.observe_response(response, url)
            except requests.RequestException as error:
                last_error = error
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_factor * (2**attempt) + random.uniform(self.delay_min, self.delay_max))
                continue
            if response.status_code not in RETRY_STATUS_CODES:
                return response
            last_response = response
            if attempt >= self.max_retries:
                break
            time.sleep(self.backoff_factor * (2**attempt) + random.uniform(self.delay_min, self.delay_max))
        if last_response is not None:
            raise ValueError(f"Request failed after retries: {last_response.status_code} {getattr(last_response, 'url', url)}")
        raise ValueError(f"Request failed after retries: {last_error} {url}")

    def append_jsonl(self, path: str | Path, item: dict[str, Any]) -> None:
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
