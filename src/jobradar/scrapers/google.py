"""Google Careers — JS-rendered, Selenium. Reference implementation of the Selenium pattern.

Ported from the original project to write via `JobRepository`. Dedup is now a DB
lookup (`repo.exists`) instead of an in-memory dict membership test.
"""

import logging
import re
import time

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from jobradar.scrapers.base import BaseScraper
from jobradar.scrapers.browser import get_browser
from jobradar.scrapers.repository import JobRepository

log = logging.getLogger(__name__)

LISTING_URL = (
    "https://www.google.com/about/careers/applications/jobs/results/"
    "?location=India&q&employment_type=FULL_TIME&sort_by=date&target_level=MID&page={page}"
)
BASE_URL = "https://www.google.com/about/careers/applications/"
ICON_WORDS = {
    "corporate_fare",
    "place",
    "bar_chart",
    "expand_more",
    "expand_less",
    "share",
    "email",
}


class GoogleScraper(BaseScraper):
    company_name = "Google"

    def scrape(self, repo: JobRepository) -> int:
        browser = get_browser()
        wait = WebDriverWait(browser, 20)
        scraped = skipped = 0
        try:
            page = 1
            while True:
                browser.get(LISTING_URL.format(page=page))
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.spHGqe li")))
                except TimeoutException:
                    break

                for _ in range(6):
                    browser.execute_script("window.scrollBy(0, 400);")
                    time.sleep(0.3)

                cards = self._parse_cards(BeautifulSoup(browser.page_source, "html.parser"))
                if not cards:
                    break

                new_cards = [c for c in cards if c["apply_url"] and not repo.exists(c["apply_url"])]
                skipped += len(cards) - len(new_cards)

                for job in new_cards:
                    job.update(self._scrape_detail(browser, wait, job["apply_url"]))
                    if repo.upsert(job):
                        scraped += 1
                    time.sleep(1)
                page += 1
        except Exception as exc:
            log.warning("Google scrape stopped early: %s", exc)
        finally:
            browser.quit()
            repo.log_run("Google", scraped)
            log.info("Google done — %d new, %d skipped", scraped, skipped)
        return scraped

    def _clean(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        for w in ICON_WORDS:
            text = text.replace(w, "")
        return text.strip()

    def _parse_cards(self, soup: BeautifulSoup) -> list[dict]:
        jobs = []
        for card in soup.select("ul.spHGqe li"):
            job = {
                "company": "Google",
                "title": "",
                "organization": "",
                "locations": "",
                "experience_level": "",
                "apply_url": "",
                "about_the_job": "",
                "minimum_qualifications": "",
                "preferred_qualifications": "",
                "responsibilities": "",
            }
            h3 = card.find("h3")
            if h3:
                job["title"] = self._clean(h3.get_text())
            org = card.select_one("span.RP7SMd")
            if org:
                for s in org.find_all("span"):
                    t = self._clean(s.get_text())
                    if t:
                        job["organization"] = t
                        break
            locs = []
            for s in card.select("span.r0wTof"):
                t = self._clean(s.get_text())
                if t and t not in locs:
                    locs.append(t)
            job["locations"] = " | ".join(locs)
            exp = card.select_one("span.wVSTAb")
            if exp:
                job["experience_level"] = self._clean(exp.get_text())
            a = card.find("a", href=re.compile(r"jobs/results/\d+"))
            if a:
                job["apply_url"] = BASE_URL + a["href"].lstrip("./")
            if job["title"]:
                jobs.append(job)
        return jobs

    def _scrape_detail(self, browser, wait, url: str) -> dict:
        detail = {
            "about_the_job": "",
            "minimum_qualifications": "",
            "preferred_qualifications": "",
            "responsibilities": "",
        }
        try:
            browser.get(url)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h2.p1N2lc")))
            time.sleep(0.8)
            soup = BeautifulSoup(browser.page_source, "html.parser")
            about = soup.select_one("div.aG5W3")
            if about:
                detail["about_the_job"] = self._clean(about.get_text())
            resp = soup.select_one("div.BDNOWe")
            if resp:
                items = [
                    f"• {self._clean(li.get_text())}"
                    for li in resp.find_all("li")
                    if li.get_text().strip()
                ]
                detail["responsibilities"] = "\n".join(items) or self._clean(resp.get_text())
            quals = soup.select_one("div.KwJkGe")
            if quals:
                parts = re.split(
                    r"Preferred qualifications\s*:?\s*",
                    quals.get_text(separator="\n"),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )
                min_raw = re.sub(
                    r"Minimum qualifications\s*:?\s*", "", parts[0], flags=re.IGNORECASE
                )
                detail["minimum_qualifications"] = "\n".join(
                    f"• {ln.strip()}" for ln in min_raw.splitlines() if ln.strip()
                )
                if len(parts) > 1:
                    detail["preferred_qualifications"] = "\n".join(
                        f"• {ln.strip()}" for ln in parts[1].splitlines() if ln.strip()
                    )
        except TimeoutException:
            log.debug("Timeout on Google detail page %s", url)
        return detail
