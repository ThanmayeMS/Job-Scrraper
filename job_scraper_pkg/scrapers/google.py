"""scrapers/google.py — Google Careers (JS-rendered, Selenium)

Selenium required — requests/BS4 returns blank page.
headless=False works better against bot detection.
Detail page must be visited individually for JD content.
"""

import re
import time
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY
from ..core.browser import get_browser

LISTING_URL = (
    "https://www.google.com/about/careers/applications/jobs/results/"
    "?location=India&q&employment_type=FULL_TIME&sort_by=date&target_level=MID&page={page}"
)
BASE_URL   = "https://www.google.com/about/careers/applications/"
ICON_WORDS = {"corporate_fare","place","bar_chart","expand_more","expand_less","share","email"}


class GoogleScraper(BaseScraper):
    company_name = "Google"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Google Careers  |  India · Full-Time · Mid-Level  (Selenium)")
        print("=" * 60)

        browser = get_browser(headless=False)
        wait    = WebDriverWait(browser, 20)
        scraped = skipped = 0

        try:
            page = 1
            while True:
                print(f"\n[*] Page {page}")
                browser.get(LISTING_URL.format(page=page))
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.spHGqe li")))
                except TimeoutException:
                    print("[*] No cards — end of results."); break

                for _ in range(6):
                    browser.execute_script("window.scrollBy(0, 400);")
                    time.sleep(0.3)
                time.sleep(1)

                soup      = BeautifulSoup(browser.page_source, "html.parser")
                all_cards = self._parse_cards(soup)
                if not all_cards: break

                new_jobs   = [j for j in all_cards if j["apply_url"] not in db]
                skip_count = len(all_cards) - len(new_jobs)
                skipped   += skip_count
                print(f"[*] {len(all_cards)} cards | {len(new_jobs)} new | {skip_count} skipped")

                for idx, job in enumerate(new_jobs, 1):
                    print(f"  [{idx}/{len(new_jobs)}] {job['title']}")
                    if job["apply_url"]:
                        job.update(self._scrape_detail(browser, wait, job["apply_url"]))
                    upsert_job(db, job)
                    scraped += 1
                    time.sleep(1)

                save_db(db)
                page += 1

        except KeyboardInterrupt:
            print("\n[!] Stopped by user.")
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            import traceback; traceback.print_exc()
        finally:
            save_db(db)
            browser.quit()
            append_log("Google", scraped)
            print(f"\n  Google → Scraped: {scraped} | Skipped: {skipped}")

        return scraped

    def _clean(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        for w in ICON_WORDS: text = text.replace(w, "")
        return text.strip()

    def _parse_cards(self, soup: BeautifulSoup) -> list:
        jobs = []
        for card in soup.select("ul.spHGqe li"):
            job = {
                "company":"Google","fetched_date":TODAY,
                "title":"","organization":"","locations":"","experience_level":"",
                "apply_url":"","about_the_job":"",
                "minimum_qualifications":"","preferred_qualifications":"","responsibilities":"",
            }
            h3 = card.find("h3")
            if h3: job["title"] = self._clean(h3.get_text())
            org = card.select_one("span.RP7SMd")
            if org:
                for s in org.find_all("span"):
                    t = self._clean(s.get_text())
                    if t: job["organization"] = t; break
            locs = []
            for s in card.select("span.r0wTof"):
                t = self._clean(s.get_text())
                if t and t not in locs: locs.append(t)
            job["locations"] = " | ".join(locs)
            exp = card.select_one("span.wVSTAb")
            if exp: job["experience_level"] = self._clean(exp.get_text())
            a = card.find("a", href=re.compile(r"jobs/results/\d+"))
            if a: job["apply_url"] = BASE_URL + a["href"].lstrip("./")
            if job["title"]: jobs.append(job)
        return jobs

    def _scrape_detail(self, browser, wait, url: str) -> dict:
        detail = {"about_the_job":"","minimum_qualifications":"",
                  "preferred_qualifications":"","responsibilities":""}
        try:
            browser.get(url)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,"h2.p1N2lc")))
            time.sleep(0.8)
            soup = BeautifulSoup(browser.page_source,"html.parser")
            about = soup.select_one("div.aG5W3")
            if about: detail["about_the_job"] = self._clean(about.get_text())
            resp = soup.select_one("div.BDNOWe")
            if resp:
                items = [f"• {self._clean(li.get_text())}" for li in resp.find_all("li") if li.get_text().strip()]
                detail["responsibilities"] = "\n".join(items) or self._clean(resp.get_text())
            quals = soup.select_one("div.KwJkGe")
            if quals:
                full  = quals.get_text(separator="\n")
                parts = re.split(r"Preferred qualifications\s*:?\s*",full,maxsplit=1,flags=re.IGNORECASE)
                min_raw = re.sub(r"Minimum qualifications\s*:?\s*","",parts[0],flags=re.IGNORECASE)
                detail["minimum_qualifications"] = "\n".join(f"• {l.strip()}" for l in min_raw.splitlines() if l.strip())
                if len(parts) > 1:
                    detail["preferred_qualifications"] = "\n".join(f"• {l.strip()}" for l in parts[1].splitlines() if l.strip())
        except TimeoutException: print("    [!] Timeout on detail page")
        except Exception as e: print(f"    [!] Error: {e}")
        return detail
