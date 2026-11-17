from __future__ import annotations

import argparse
from pathlib import Path

from config import AppConfig
from scrapers.journal_list_loader import load_journal_names
from scrapers.journal_shards import select_journal_shard, split_journals
from scrapers.source_search_runner import SourceSearchScraper


DEFAULT_SOURCE_MARKDOWN = Path("data/CSSCI来源刊公众号主页链接.md")



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", action="append", dest="journals", default=[])
    parser.add_argument("--limit-journals", type=int, default=0)
    parser.add_argument("--limit-articles", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--relation-page-size", type=int, default=10)
    parser.add_argument("--max-relation-pages", type=int, default=0)
    parser.add_argument("--delay-min", type=float, default=5.0)
    parser.add_argument("--delay-max", type=float, default=8.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--backoff-factor", type=float, default=12.0)
    parser.add_argument("--auto-detect-years", action="store_true", default=False)
    parser.add_argument("--year-end", type=int, default=2026)
    parser.add_argument("--year-start-floor", type=int, default=2016)
    parser.add_argument("--slice-by-year", action="store_true", default=False, help="已废弃，请使用 --auto-detect-years")
    parser.add_argument("--year-start", type=int, default=2023, help="仅在非 --auto-detect-years 模式下生效")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--shard-id", type=int, default=1)
    parser.add_argument("--shard-count", type=int, default=3)
    parser.add_argument("--shard-output-root", default="output")
    parser.add_argument("--shard-cache-root", default="cache")
    parser.add_argument("--shard-log-root", default="logs")
    parser.add_argument("--print-shards", action="store_true", default=False)
    parser.add_argument("--efficiency-pilot", action="store_true", default=False, help="Opt-in persistent plans/pages and delayed retry queue")
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    source_markdown = (base_dir / DEFAULT_SOURCE_MARKDOWN).resolve()
    config = AppConfig(base_dir=base_dir, source_markdown=source_markdown)

    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    journal_names = load_journal_names(config.source_markdown)
    if args.journals:
        journal_names = list(args.journals)
    elif args.limit_journals > 0:
        journal_names = journal_names[: args.limit_journals]

    if args.print_shards:
        for index, shard_journals in enumerate(split_journals(journal_names, args.shard_count), start=1):
            print(f"shard-{index}: {len(shard_journals)} journals")
            for journal_name in shard_journals:
                print(f"  - {journal_name}")
        return 0

    journal_names = select_journal_shard(journal_names, args.shard_id, args.shard_count)
    shard_name = f"shard-{args.shard_id}" if args.shard_count > 1 else ""
    output_dir = (base_dir / args.shard_output_root / shard_name).resolve() if shard_name else (base_dir / args.shard_output_root).resolve()
    cache_dir = (base_dir / args.shard_cache_root / shard_name).resolve() if shard_name else (base_dir / args.shard_cache_root).resolve()
    logs_dir = (base_dir / args.shard_log_root / shard_name).resolve() if shard_name else base_dir / args.shard_log_root
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    scraper_class = SourceSearchScraper
    if args.efficiency_pilot:
        if not args.resume:
            raise ValueError("--efficiency-pilot requires --resume")
        from scrapers.efficiency_pilot import PilotScraper
        scraper_class = PilotScraper
    scraper = scraper_class(
        output_dir=output_dir,
        cache_dir=cache_dir,
        env_path=base_dir / ".env",
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_retries=args.max_retries,
        backoff_factor=args.backoff_factor,
        checkpoint_path=output_dir / "checkpoint.json",
        shard_id=args.shard_id,
        shard_count=args.shard_count,
    )
    year_slices = None
    auto_detect_years = args.auto_detect_years
    year_end = args.year_end
    year_start_floor = args.year_start_floor

    if args.slice_by_year and not auto_detect_years:
        # 旧模式：固定年份范围
        year_slices = [str(year) for year in range(args.year_end, args.year_start - 1, -1)]

    while True:
        scheduled = scraper.scheduled_journals(journal_names) if args.efficiency_pilot else journal_names
        if scheduled:
            scraper.run(
                journal_names=scheduled,
                page_size=args.page_size,
                max_pages=args.max_pages,
                relation_page_size=args.relation_page_size,
                max_relation_pages=args.max_relation_pages,
                limit_articles=args.limit_articles,
                resume=args.resume,
                year_slices=year_slices,
                auto_detect_years=auto_detect_years,
                year_end=year_end,
                year_start_floor=year_start_floor,
                collect_results=not args.resume,
            )
        if not args.efficiency_pilot or scraper.blocked_reason:
            break
        wait = scraper.pending_retry_wait()
        if wait is None:
            break
        scraper.event("queue_wait", seconds=wait)
        import time
        time.sleep(wait)
    if scraper.blocked_reason == "verification_required":
        return 75
    if scraper.blocked_reason == "cookie_replacement_required":
        return 76
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
