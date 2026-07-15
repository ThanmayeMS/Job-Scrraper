"""Scraper plugin registry.

Adding a company is still a one-liner: import the class and add a registry entry.
Amazon (API) and Google (Selenium) are the two reference implementations. The other
six companies from the original project (jpmorgan, goldman, mastercard, visa,
microsoft, citi) port mechanically:

    1. Copy the legacy scraper module in here.
    2. Replace `upsert_job(db, job)` -> `repo.upsert(job)` and
       `url in db`          -> `repo.exists(url)`.
    3. Register the class below.

Their pagination / platform logic is unchanged — only the persistence calls move.
"""

from jobradar.scrapers.amazon import AmazonScraper
from jobradar.scrapers.base import BaseScraper
from jobradar.scrapers.google import GoogleScraper

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "amazon": AmazonScraper,
    "google": GoogleScraper,
}

# Classification for the runner: API scrapers are safe to run concurrently,
# Selenium scrapers must run sequentially (one browser each).
API_SCRAPERS: set[str] = {"amazon"}
SELENIUM_SCRAPERS: set[str] = {"google"}

__all__ = ["API_SCRAPERS", "SCRAPER_REGISTRY", "SELENIUM_SCRAPERS", "BaseScraper"]
