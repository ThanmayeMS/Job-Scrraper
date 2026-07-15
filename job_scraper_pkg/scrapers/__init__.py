"""
scrapers/__init__.py

SCRAPER_REGISTRY maps the string key used in COMPANIES_TO_RUN
to the scraper class. Adding a new company = one import + one dict entry here.
"""

from .google      import GoogleScraper
from .amazon      import AmazonScraper
from .jpmorgan    import JPMorganScraper
from .goldman     import GoldmanScraper
from .mastercard  import MastercardScraper
from .visa        import VisaScraper
from .microsoft   import MicrosoftScraper
from .citi        import CitiScraper

SCRAPER_REGISTRY: dict = {
    "google":     GoogleScraper,
    "amazon":     AmazonScraper,
    "jpmorgan":   JPMorganScraper,
    "goldman":    GoldmanScraper,
    "mastercard": MastercardScraper,
    "visa":       VisaScraper,
    "microsoft":  MicrosoftScraper,
    "citi":       CitiScraper,
}
