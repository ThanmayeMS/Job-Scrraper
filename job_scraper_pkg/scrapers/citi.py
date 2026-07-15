"""scrapers/citi.py — Citi (Phenom People SSR, fully JS-rendered)

Architecture notes (from SCRAPER_ARCHITECTURE.md):
- Zero XHR — Selenium required for both listing and detail
- URL params ignored by server — pagination by clicking a.next
- a.next stays in DOM even on last page — stop by detecting cards unchanged
- 2-pass: collect all stubs first, then detail scrape
  (interleaving breaks because returning to listing URL always reloads page 1)
- JD headings vary per job — stored as single job_description field
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

LISTING_URL  = "https://jobs.citi.com/search-jobs/India/287/2/1269750/22/79/50/2"
BASE_URL     = "https://jobs.citi.com"
SKIP_HEADINGS = {"discover your future at citi", "shape your career with citi"}


class CitiScraper(BaseScraper):
    company_name = "Citi"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Citi  |  India — Phenom People (fully JS-rendered)")
        print("  2-pass: collect all 51 pages, then scrape details")
        print("=" * 60)

        browser = get_browser(headless=False)
        wait    = WebDriverWait(browser, 25)
        scraped = skipped = 0

        try:
            # ── PASS 1: Walk all listing pages ─────────────────────────────────
            print("\n[PASS 1] Collecting stubs from all listing pages...")
            all_stubs: dict = {}
            page = 1

            browser.get(LISTING_URL)
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.sr-job-item__link")))
            except TimeoutException:
                print("[!] No cards on first load — aborting.")
                return 0

            while True:
                time.sleep(1.5)
                soup  = BeautifulSoup(browser.page_source, "html.parser")
                cards = self._parse_listing_page(soup)

                print(f"  Page {page}: {len(cards)} cards")
                for stub in cards:
                    url = stub["apply_url"]
                    if url and url not in all_stubs:
                        all_stubs[url] = stub

                if not soup.select_one("a.next"):
                    print(f"  No 'Next' on page {page} — listing complete.")
                    break

                try:
                    first_url = cards[0]["apply_url"] if cards else ""
                    next_btn  = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.next")))
                    browser.execute_script("arguments[0].click();", next_btn)

                    page_changed = False
                    for _ in range(20):
                        time.sleep(0.5)
                        new_soup  = BeautifulSoup(browser.page_source, "html.parser")
                        new_cards = self._parse_listing_page(new_soup)
                        if new_cards and new_cards[0]["apply_url"] != first_url:
                            page_changed = True
                            break

                    if not page_changed:
                        print(f"  Cards unchanged after Next on page {page} — listing complete.")
                        break

                    page += 1
                except Exception as e:
                    print(f"  [!] Next click failed on page {page}: {e}")
                    break

            print(f"\n[PASS 1] Done — {len(all_stubs)} unique jobs across {page} pages")

            # ── PASS 2: Detail scrape for new jobs only ─────────────────────────
            new_stubs = [s for s in all_stubs.values() if s["apply_url"] not in db]
            skipped   = len(all_stubs) - len(new_stubs)
            print(f"\n[PASS 2] {len(new_stubs)} new | {skipped} already in DB")

            for idx, stub in enumerate(new_stubs, 1):
                print(f"  [{idx}/{len(new_stubs)}] {stub['title']}")
                detail = self._scrape_detail(browser, wait, stub["apply_url"])
                stub.update(detail)
                upsert_job(db, stub)
                scraped += 1
                if scraped % 10 == 0:
                    save_db(db)
                time.sleep(0.8)

        except KeyboardInterrupt:
            print("\n[!] Stopped by user.")
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            import traceback; traceback.print_exc()
        finally:
            save_db(db)
            browser.quit()
            append_log("Citi", scraped)
            print(f"\n  Citi → Scraped: {scraped} | Skipped: {skipped}")

        return scraped

    def _parse_listing_page(self, soup: BeautifulSoup) -> list:
        jobs = []
        for card in soup.select("a.sr-job-item__link"):
            href  = card.get("href", "")
            title = re.sub(r"\s+", " ", card.get_text()).strip()
            if not href or not title:
                continue
            apply_url = BASE_URL + href if href.startswith("/") else href
            job_id    = card.get("data-job-id", "") or href.rstrip("/").split("/")[-1]
            wrapper   = card.find_parent(class_=re.compile(r"sr-job-item"))
            locations = work_type = ""
            if wrapper:
                loc_el = wrapper.select_one("span.job-location, [class*='location']")
                if loc_el: locations = re.sub(r"\s+", " ", loc_el.get_text()).strip()
                wt_el  = wrapper.select_one("span.work-type-label, [class*='work-type']")
                if wt_el: work_type = re.sub(r"\s+", " ", wt_el.get_text()).strip()
                if not work_type:
                    for span in wrapper.find_all("span"):
                        t = span.get_text().strip()
                        if t.lower() in {"hybrid","on-site","remote","onsite"}:
                            work_type = t; break
            jobs.append({
                "company": "Citi", "fetched_date": TODAY,
                "title": title, "job_id": str(job_id),
                "apply_url": apply_url, "locations": locations, "work_type": work_type,
            })
        return jobs

    def _scrape_detail(self, browser, wait, url: str) -> dict:
        empty = {"job_description": ""}
        try:
            browser.get(url)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.ats-description")))
            time.sleep(0.8)
            return self._parse_detail(BeautifulSoup(browser.page_source, "html.parser"))
        except TimeoutException:
            print("    [!] Timeout on div.ats-description")
            return empty
        except Exception as e:
            print(f"    [!] Detail error: {e}")
            return empty

    def _parse_detail(self, soup: BeautifulSoup) -> dict:
        for el in soup.find_all(attrs={"aria-hidden": "true"}): el.decompose()
        container = (soup.select_one("div.ats-description")
                     or soup.select_one("section.job-description")
                     or soup.find("main"))
        if not container: return {"job_description": ""}

        current_heading = None
        skip_current    = False
        sections: list  = []

        for tag in container.children:
            if not hasattr(tag, "name") or not tag.name: continue
            if tag.name == "h2" and "jd-evergreen-hl" in (tag.get("class") or []):
                heading      = re.sub(r"\s+", " ", tag.get_text()).strip()
                skip_current = heading.lower() in SKIP_HEADINGS
                current_heading = None if skip_current else heading
                if current_heading: sections.append((current_heading, []))
                continue
            if skip_current or current_heading is None: continue
            lines = sections[-1][1]
            if tag.name == "p":
                text = re.sub(r"\s+", " ", tag.get_text()).strip()
                if text: lines.append(text)
            elif tag.name in {"ul","ol"}:
                for li in tag.find_all("li", recursive=True):
                    text = re.sub(r"\s+", " ", li.get_text()).strip()
                    if text: lines.append("• " + text)
            elif tag.name in {"div","section"}:
                for child in tag.find_all(["p","li"]):
                    text = re.sub(r"\s+", " ", child.get_text()).strip()
                    if text: lines.append(("• " if child.name == "li" else "") + text)

        blocks = []
        for heading, lines in sections:
            blocks.append(heading + ("\n" + "\n".join(lines) if lines else ""))
        return {"job_description": "\n\n".join(blocks)}
