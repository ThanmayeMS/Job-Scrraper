"""
job_scraper_pkg/__main__.py — Orchestration runner

Run with:
    python -m job_scraper_pkg --parallel                     # run all companies
    python -m job_scraper_pkg --companies google citi # run specific companies
    python -m job_scraper_pkg --list                  # list all registered scrapers
    python -m job_scraper_pkg --parallel              # run API scrapers concurrently

Architecture:
    Each scraper is a self-contained class implementing BaseScraper.scrape(db).
    The runner loads the shared db once, passes it to each scraper, then saves
    a final summary. Thread-safety is guaranteed by core/db.py's _db_lock.

Parallelism strategy:
    --parallel flag runs API-based scrapers concurrently (Amazon, JPMorgan,
    Goldman, Visa, Microsoft) while Selenium scrapers (Google, Mastercard, Citi)
    run sequentially after. Selenium scrapers can't safely share a browser
    instance across threads, and running multiple browsers simultaneously
    risks bot detection and high memory usage.
"""

import argparse
import sys
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

from .core.db import load_db, save_db, load_log, flush_recent, DB_FILE, LOG_FILE
from .scrapers import SCRAPER_REGISTRY

TODAY = date.today().strftime("%Y-%m-%d")

# ── Default run list ──────────────────────────────────────────────────────────
# Edit this to control which companies run by default.
COMPANIES_TO_RUN = [
    "google",
    "amazon",
    "jpmorgan",
    "goldman",
    "mastercard",
    "visa",
    "microsoft",
    "citi",
]

# ── Recent jobs window ─────────────────────────────────────────────────────────
# jobs_recent.json will contain only jobs fetched within this many days.
# Change this number to widen or narrow the window.
# 3  = last 3 days  (tight, only very fresh jobs)
# 7  = last 7 days  (recommended for daily runs)
# 30 = last 30 days (broader, good after a gap in running)
RECENT_DAYS = 7

# ── Scraper type classification ───────────────────────────────────────────────
# API scrapers: safe to run concurrently (requests-based, no shared browser)
# Selenium scrapers: must run sequentially (one browser per scraper)
API_SCRAPERS      = {"amazon", "jpmorgan", "goldman", "visa", "microsoft"}
SELENIUM_SCRAPERS = {"google", "mastercard", "citi"}


def print_summary(db: dict, elapsed: float, results: dict):
    print("\n" + "=" * 60)
    print(f"  SUMMARY  |  {TODAY}  |  Total in DB: {len(db)}")
    print(f"  Time     : {int(elapsed//60)}m{int(elapsed%60)}s")
    print(f"{'─'*60}")
    print(f"  {'Company':<25} {'New Jobs':>8}  {'Status'}")
    print(f"  {'─'*25} {'─'*8}  {'─'*10}")
    for company, info in sorted(results.items()):
        new_jobs = info.get("new_jobs", 0)
        status   = info.get("status", "ok")
        print(f"  {company:<25} {new_jobs:>8}  {status}")
    print(f"{'─'*60}")

    # Count by company in db
    by_company: dict = {}
    for job in db.values():
        c = job.get("company", "Unknown")
        by_company[c] = by_company.get(c, 0) + 1
    print(f"\n  {'Company':<25} {'Total in DB':>12}")
    print(f"  {'─'*25} {'─'*12}")
    for c, count in sorted(by_company.items()):
        print(f"  {c:<25}: {count:>4} jobs")

    # Daily log tail
    log = load_log(LOG_FILE)
    if log:
        print(f"\n  ── Daily Log (last 10) {'─'*30}")
        print(f"  {'Date':<12} {'Company':<22} {'New Jobs':>8}")
        print(f"  {'─'*12} {'─'*22} {'─'*8}")
        for entry in log[:10]:
            print(f"  {entry.get('date',''):<12} {entry.get('company',''):<22} {entry.get('new_jobs',0):>8}")
    print("=" * 60)


def run_scraper(key: str, db: dict) -> dict:
    """Run a single scraper by key. Returns result info dict."""
    cls = SCRAPER_REGISTRY.get(key)
    if not cls:
        return {"new_jobs": 0, "status": "unknown scraper"}
    try:
        scraper  = cls()
        new_jobs = scraper.scrape(db)
        return {"new_jobs": new_jobs, "status": "ok"}
    except Exception as e:
        print(f"\n[!] {key} scraper failed: {e}")
        import traceback; traceback.print_exc()
        return {"new_jobs": 0, "status": f"error: {e}"}


def main():
    parser = argparse.ArgumentParser(
        description="Multi-company job scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m job_scraper_pkg                            # run all companies
  python -m job_scraper_pkg --companies amazon google  # run specific companies
  python -m job_scraper_pkg --parallel                 # run API scrapers concurrently
  python -m job_scraper_pkg --list                     # show registered scrapers
        """
    )
    parser.add_argument(
        "--companies", nargs="+", metavar="COMPANY",
        help="Companies to scrape (space-separated). Defaults to COMPANIES_TO_RUN list."
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Run API scrapers concurrently (Amazon, JPMorgan, Goldman, Visa, Microsoft). "
             "Selenium scrapers always run sequentially."
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all registered scrapers and exit."
    )
    args = parser.parse_args()

    if args.list:
        print("\nRegistered scrapers:")
        for key, cls in sorted(SCRAPER_REGISTRY.items()):
            stype = "API" if key in API_SCRAPERS else "Selenium"
            print(f"  {key:<15} ({stype})  →  {cls.__name__}")
        print()
        return

    companies = args.companies if args.companies else COMPANIES_TO_RUN

    # Validate
    invalid = [c for c in companies if c not in SCRAPER_REGISTRY]
    if invalid:
        print(f"[!] Unknown companies: {invalid}")
        print(f"    Valid: {sorted(SCRAPER_REGISTRY.keys())}")
        sys.exit(1)

    print("=" * 60)
    print(f"  Job Scraper  |  {TODAY}")
    print(f"  Companies : {', '.join(companies)}")
    print(f"  Mode      : {'parallel API + sequential Selenium' if args.parallel else 'sequential'}")
    print(f"  DB        : {DB_FILE}")
    print(f"  Log       : {LOG_FILE}")
    print("=" * 60)

    db      = load_db(DB_FILE)
    print(f"[*] Loaded {len(db)} existing jobs.\n")
    results = {}
    start   = time.time()

    if args.parallel:
        # Split into API (concurrent) and Selenium (sequential)
        api_keys      = [c for c in companies if c in API_SCRAPERS]
        selenium_keys = [c for c in companies if c in SELENIUM_SCRAPERS]

        # Run API scrapers concurrently
        if api_keys:
            print(f"[*] Running API scrapers concurrently: {api_keys}")
            with ThreadPoolExecutor(max_workers=len(api_keys)) as pool:
                future_map = {pool.submit(run_scraper, key, db): key for key in api_keys}
                for future in as_completed(future_map):
                    key = future_map[future]
                    try:
                        results[key] = future.result()
                    except Exception as e:
                        results[key] = {"new_jobs": 0, "status": f"error: {e}"}

        # Run Selenium scrapers sequentially (after API scrapers finish)
        if selenium_keys:
            print(f"\n[*] Running Selenium scrapers sequentially: {selenium_keys}")
            for key in selenium_keys:
                results[key] = run_scraper(key, db)
    else:
        # Simple sequential run
        for key in companies:
            results[key] = run_scraper(key, db)

    save_db(db)
    print_summary(db, time.time() - start, results)
    flush_recent(db, RECENT_DAYS)


if __name__ == "__main__":
    main()
