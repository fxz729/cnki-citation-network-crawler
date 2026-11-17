from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scrapers.checkpoint import load_checkpoint, save_checkpoint, stamp_checkpoint
from scrapers.source_search_runner import (
    CnkiCookieReplacementRequired,
    SourceSearchScraper,
    sanitize_error_message,
)
from notifications import send_cookie_replacement_email

MIN_PROBE_DELAY_MINUTES = 15
MAX_PROBE_DELAY_MINUTES = 120


def schedule_next_probe(
    checkpoint: dict[str, Any],
    now: datetime,
    verification_still_present: bool,
) -> dict[str, Any]:
    if not verification_still_present:
        return {
            **checkpoint,
            "verification_probe_next_at": "",
            "verification_probe_delay_minutes": 0,
        }
    previous = int(checkpoint.get("verification_probe_delay_minutes", 0) or 0)
    if previous < MIN_PROBE_DELAY_MINUTES:
        delay = MIN_PROBE_DELAY_MINUTES
    elif previous < MAX_PROBE_DELAY_MINUTES:
        delay = min(previous * 2, MAX_PROBE_DELAY_MINUTES)
    else:
        delay = MAX_PROBE_DELAY_MINUTES
    return {
        **checkpoint,
        "verification_probe_delay_minutes": delay,
        "verification_probe_next_at": (
            now + timedelta(minutes=delay)
        ).isoformat(),
    }


def systemctl_start(service_name: str) -> None:
    subprocess.run(
        ["systemctl", "start", service_name],
        check=True,
        capture_output=True,
        text=True,
    )


def service_is_active(shard_id: int) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", f"cnki-scraper3@{shard_id}.service"],
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _probe_due(checkpoint: dict[str, Any], now: datetime) -> bool:
    value = str(checkpoint.get("verification_probe_next_at") or "")
    if not value:
        return False
    try:
        return now >= datetime.fromisoformat(value)
    except ValueError:
        return True


def _cookie_fingerprint(scraper: SourceSearchScraper) -> str:
    return scraper._cookie_fingerprint()


def _is_valid_detail_probe(body: str) -> bool:
    normalized = body.lower()
    if not body.strip() or any(
        marker in normalized
        for marker in ("/verify/", "安全验证", "验证码", "登录已过期", "登录失效")
    ):
        return False
    has_link = "paramkcmslink" in normalized
    has_summary = "chdivsummary" in normalized
    return has_link and has_summary


def probe_shard(
    shard_id: int,
    shard_count: int,
    checkpoint: dict[str, Any],
    checkpoint_path: str | Path,
    now: datetime,
) -> str:
    if checkpoint.get("blocked_reason") != "verification_required":
        if checkpoint.get("verification_probe_result") == "ready":
            save_checkpoint(
                checkpoint_path,
                stamp_checkpoint({**checkpoint, "verification_probe_result": ""}),
            )
        return "ignored"
    if service_is_active(shard_id):
        return "ignored"
    if not checkpoint.get("verification_probe_next_at"):
        updated = stamp_checkpoint(
            schedule_next_probe(checkpoint, now, verification_still_present=True)
        )
        save_checkpoint(checkpoint_path, updated)
        return "scheduled"
    if not _probe_due(checkpoint, now):
        return "not_due"

    scraper = SourceSearchScraper(
        output_dir=Path("output-3") / f"shard-{shard_id}",
        cache_dir=Path("cache-3") / f"shard-{shard_id}",
        env_path=Path(".env"),
        shard_id=shard_id,
        shard_count=shard_count,
    )
    journal_name = str(checkpoint.get("current_journal") or "").strip()
    try:
        results, total = scraper.search_page(journal_name, page=1, page_size=50)
        if total <= 0 or not results or not results[0].get("detail_url"):
            raise RuntimeError("fresh search returned no probe article")
        detail_body = scraper.fetch_detail_page(results[0])
        if not _is_valid_detail_probe(detail_body):
            raise RuntimeError("fresh detail response is not a valid article page")
    except CnkiCookieReplacementRequired as error:
        saved_fingerprint = checkpoint.get("cookie_replacement_cookie_fingerprint")
        current_fingerprint = _cookie_fingerprint(scraper)
        already_notified = (
            bool(checkpoint.get("cookie_replacement_notified"))
            and saved_fingerprint == current_fingerprint
        )
        notified = already_notified
        if not already_notified:
            notified = send_cookie_replacement_email(
                shard_id=shard_id,
                reason=sanitize_error_message(error),
                env_path=Path(".env"),
            )
        updated = stamp_checkpoint({
            **checkpoint,
            "blocked_reason": "cookie_replacement_required",
            "blocked_message": sanitize_error_message(error),
            "verification_probe_result": "cookie_required",
            "cookie_replacement_notified": notified,
            "cookie_replacement_cookie_fingerprint": current_fingerprint,
        })
        save_checkpoint(checkpoint_path, updated)
        return "cookie_required"
    except Exception:
        updated = stamp_checkpoint(
            schedule_next_probe(checkpoint, now, verification_still_present=True)
        )
        save_checkpoint(checkpoint_path, updated)
        return "deferred"

    updated = stamp_checkpoint(
        schedule_next_probe(
            {
                **checkpoint,
                "blocked_reason": "",
                "blocked_message": "",
                "verification_probe_result": "ready",
            },
            now,
            verification_still_present=False,
        )
    )
    save_checkpoint(checkpoint_path, updated)
    return "ready"


def start_if_ready(shard_id: int, checkpoint: dict[str, Any]) -> bool:
    if (
        checkpoint.get("blocked_reason")
        or checkpoint.get("verification_probe_result") != "ready"
    ):
        return False
    if service_is_active(shard_id):
        return False
    systemctl_start(f"cnki-scraper3@{shard_id}.service")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=3)
    parser.add_argument("--checkpoint", default="output-3/shard-{shard_id}/checkpoint.json")
    parser.add_argument("--start-if-ready", action="store_true")
    args = parser.parse_args()
    checkpoint_path = Path(args.checkpoint.format(shard_id=args.shard_id))
    checkpoint = load_checkpoint(checkpoint_path)
    if args.start_if_ready:
        start_if_ready(args.shard_id, checkpoint)
        return 0
    now = datetime.now().astimezone()
    probe_shard(args.shard_id, args.shard_count, checkpoint, checkpoint_path, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
