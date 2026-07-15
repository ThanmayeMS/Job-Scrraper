"""
base.py — Contract that every company scraper must implement.

Adding a new company = create scrapers/<company>.py, subclass BaseScraper,
implement scrape(). Register one line in runner.py. That's it.
"""

from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """
    Every scraper implements exactly one method: scrape().
    The runner calls scraper.scrape(db) and expects an int (new jobs added).

    db is the shared jobs dict, keyed by apply_url.
    Thread-safety for db writes is handled by core/db.py — scrapers just call upsert_job().
    """

    # Set this in every subclass — used in logging and COMPANIES_TO_RUN mapping
    company_name: str = ""

    @abstractmethod
    def scrape(self, db: dict) -> int:
        """
        Run the full scrape for this company.
        Must handle its own pagination, detail fetching, and error recovery.
        Returns the number of NEW jobs added to db this run.
        """
        raise NotImplementedError
