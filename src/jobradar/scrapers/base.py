"""Contract every company scraper implements.

Unchanged philosophy from the original project: one method, `scrape()`. The only
difference is that persistence now goes through a `JobRepository` (Postgres) instead
of a shared in-memory dict + JSON file.
"""

from abc import ABC, abstractmethod

from jobradar.scrapers.repository import JobRepository


class BaseScraper(ABC):
    company_name: str = ""

    @abstractmethod
    def scrape(self, repo: JobRepository) -> int:
        """Run the full scrape. Return the number of NEW jobs inserted."""
        raise NotImplementedError
