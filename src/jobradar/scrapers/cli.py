"""CLI entry point:  jobradar-scrape --companies amazon google  (or --list).

A thin local runner mirroring the legacy `python -m job_scraper_pkg`. In production
the same scrapers are driven by Celery tasks (see jobradar.workers.tasks).
"""

import argparse

from jobradar.db.base import SessionLocal
from jobradar.logging_config import configure_logging
from jobradar.scrapers import SCRAPER_REGISTRY
from jobradar.scrapers.repository import JobRepository


def run_company(key: str) -> int:
    scraper_cls = SCRAPER_REGISTRY[key]
    db = SessionLocal()
    try:
        return scraper_cls().scrape(JobRepository(db))
    finally:
        db.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run JobRadar scrapers")
    parser.add_argument("--companies", nargs="+", metavar="KEY", help="scraper keys to run")
    parser.add_argument("--list", action="store_true", help="list registered scrapers")
    args = parser.parse_args()

    if args.list:
        for key, cls in sorted(SCRAPER_REGISTRY.items()):
            print(f"  {key:<12} -> {cls.__name__}")
        return

    companies = args.companies or list(SCRAPER_REGISTRY)
    for key in companies:
        if key not in SCRAPER_REGISTRY:
            print(f"[!] Unknown scraper: {key}")
            continue
        count = run_company(key)
        print(f"[✓] {key}: {count} new jobs")


if __name__ == "__main__":
    main()
