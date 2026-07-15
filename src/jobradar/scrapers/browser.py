"""Shared Selenium Chrome factory. Honors SELENIUM_HEADLESS and CHROME_BIN."""

import os

from selenium import webdriver

from jobradar.config import settings

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get_browser(headless: bool | None = None) -> webdriver.Chrome:
    headless = settings.selenium_headless if headless is None else headless
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(f"user-agent={_UA}")

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin

    return webdriver.Chrome(options=opts)
