"""
core/browser.py — Shared Selenium browser factory.

All Selenium scrapers call get_browser() to get a configured Chrome instance.
Centralised here so any Chrome option change (headless, proxy, UA) is one edit.
"""

from selenium import webdriver


def get_browser(headless: bool = False) -> webdriver.Chrome:
    """
    Return a configured Chrome WebDriver.
    headless=False by default — works better against bot detection on most sites.
    Pass headless=True for sites confirmed to work headlessly.
    """
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)
