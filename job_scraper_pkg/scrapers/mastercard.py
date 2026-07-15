"""scrapers/mastercard.py — Mastercard (Phenom People API + Selenium detail)

Listing: POST to /widgets — requires PHPPPE_ACT + PLAY_SESSION cookies (seed with GET first)
Detail:  Selenium — div.jd-info is JS-rendered
Page size hard-capped at 5 by server.

KEY FIXES vs previous broken version:
- Payload has full field set (pageName, pageId, siteType, keywords, subsearch, jobs, counts, global)
- eid passed as empty string "" not None when missing
- s=1 hardcoded in URL, page_num increments only in payload
- Duplicate detection runs AFTER processing (not before — avoids stopping on re-runs)
"""

import time
import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY
from ..core.browser import get_browser

SEARCH_BASE     = (
    "https://careers.mastercard.com/widgets"
    "?refNum=MASRUS&locale=en_us&siteType=external&pageId=page11&channel=desktop"
)
JOB_DETAIL_BASE = "https://careers.mastercard.com/us/en/job"
PAGE_SIZE       = 5

HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json, text/plain, */*",
    "User-Agent":   (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://careers.mastercard.com/us/en/search-results",
    "Origin":  "https://careers.mastercard.com",
}

SECTION_MAP = {
    "overview":                 "overview",
    "about the role":           "role",
    "role":                     "role",
    "all about you":            "all_about_you",
    "minimum qualifications":   "all_about_you",
    "required qualifications":  "all_about_you",
    "desirable":                "qualifications",
    "preferred qualifications": "qualifications",
    "essential skills":         "qualifications",
    "nice to have":             "qualifications",
    "corporate security":       None,
}


class MastercardScraper(BaseScraper):
    company_name = "Mastercard"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Mastercard  |  India (Phenom People + Selenium detail)")
        print("=" * 60)

        session = requests.Session()
        session.headers.update(HEADERS)

        # Seed session — picks up PHPPPE_ACT and PLAY_SESSION cookies
        # Without these the server ignores from= offset and returns page 1 every time
        print("[*] Seeding session cookies...")
        try:
            seed = session.get(
                "https://careers.mastercard.com/us/en/search-results",
                timeout=15
            )
            print(f"[*] Seed: {seed.status_code} | Cookies: {list(session.cookies.keys())}")
        except Exception as e:
            print(f"[!] Seed failed: {e} — pagination may not work correctly")

        print("[*] Starting browser for job detail pages...")
        browser = get_browser(headless=True)
        wait    = WebDriverWait(browser, 15)

        scraped         = skipped = 0
        total_hits      = None
        from_offset     = 0
        page_num        = 1
        eid_token       = None
        seen_apply_urls = set()

        try:
            while True:
                print(f"\n[*] page={page_num} from={from_offset}" +
                      (f" / {total_hits} total" if total_hits is not None else ""))

                try:
                    # s=1 hardcoded in URL — page_num increments in payload only
                    search_url   = f"{SEARCH_BASE}&from={from_offset}&s=1"
                    page_referer = f"https://careers.mastercard.com/us/en/search-results?from={from_offset}&s=1"
                    session.headers.update({"Referer": page_referer})
                    resp = session.post(
                        search_url,
                        json=self._payload(from_offset, page_num, eid_token),
                        timeout=20
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except requests.RequestException as e:
                    print(f"[!] Listing request failed: {e}"); break
                except ValueError:
                    print(f"[!] Non-JSON response (status {resp.status_code})"); break

                eager     = (
                    data.get("eagerLoadRefineSearch")
                    or data.get("eagerLoadRefineSearchSession")
                    or {}
                )
                inner     = eager.get("data", {})
                jobs_list = inner.get("jobs") or []

                if total_hits is None:
                    total_hits = int(eager.get("totalHits", 0) or 0)
                    print(f"[*] totalHits = {total_hits}")

                # Capture eid token from page 1 — required for correct pagination
                if page_num == 1 and not eid_token:
                    eid_data  = eager.get("eid") or {}
                    eid_token = eid_data.get("eid") or eid_data.get("searchId") or None
                    if eid_token:
                        print(f"[*] eid token: {eid_token[:16]}...")

                print(f"[*] {len(jobs_list)} jobs on this page")

                if not jobs_list:
                    print("[*] Empty page — done."); break

                new_n = skip_n = 0
                for item in jobs_list:
                    apply_url = item.get("applyUrl", "").strip()
                    title     = item.get("title",    "").strip()
                    req_id    = item.get("reqId",    "").strip()

                    if not apply_url or not title:
                        continue

                    if apply_url in db:
                        skip_n += 1; skipped += 1
                        continue

                    raw_locs  = item.get("multi_location") or []
                    locations = (
                        " | ".join(str(l) for l in raw_locs if l)
                        if raw_locs
                        else ", ".join(filter(None, [
                            item.get("city", ""), item.get("country", "")
                        ]))
                    )

                    posted = item.get("postedDate", "")
                    if posted and "T" in posted:
                        posted = posted[:10]

                    if not req_id:
                        req_id = self._extract_req_id(apply_url)

                    try:
                        apath = apply_url.rstrip("/")
                        if apath.endswith("/apply"):
                            apath = apath[:-6]
                        slug = apath.split("/")[-1]
                    except Exception:
                        slug = req_id

                    print(f"  -> {title} [{req_id}]")
                    detail = self._fetch_detail(req_id, slug, browser, wait)

                    job = {
                        "company":            "Mastercard",
                        "fetched_date":       TODAY,
                        "title":              title,
                        "req_id":             req_id,
                        "category":           item.get("category", "").strip(),
                        "locations":          locations,
                        "job_type":           item.get("type", "").strip(),
                        "posted_date":        posted,
                        "apply_url":          apply_url,
                        "description_teaser": item.get("descriptionTeaser", "").strip(),
                        "overview":           detail["overview"],
                        "role":               detail["role"],
                        "all_about_you":      detail["all_about_you"],
                        "qualifications":     detail["qualifications"],
                    }

                    upsert_job(db, job)
                    new_n += 1; scraped += 1
                    print(f"  OK {title} | {locations}")
                    time.sleep(0.5)

                print(f"[*] {new_n} new | {skip_n} skipped")

                # Duplicate detection AFTER processing — avoids stopping on re-runs
                # where all jobs are already in DB (would look like duplicate page)
                page_urls = set(item.get("applyUrl", "") for item in jobs_list)
                if page_urls and page_urls.issubset(seen_apply_urls):
                    print("[*] Duplicate page content — pagination stuck, stopping.")
                    break
                seen_apply_urls.update(page_urls)

                if new_n > 0:
                    save_db(db)

                from_offset += PAGE_SIZE
                page_num    += 1

                if total_hits and from_offset >= total_hits:
                    print(f"[*] Reached totalHits ({total_hits}) — done.")
                    break

                time.sleep(0.8)

        finally:
            browser.quit()

        save_db(db)
        append_log("Mastercard", scraped)
        print(f"\n  Mastercard → Scraped: {scraped} | Skipped: {skipped}")
        return scraped

    def _payload(self, from_offset: int, page_num: int, eid: str = None) -> dict:
        """
        Full payload matching exactly what the browser sends.
        eid passed as empty string when not yet captured (not None).
        s increments with each page.
        Always uses ddoKey=eagerLoadRefineSearch.
        """
        return {
            "lang":       "en_us",
            "deviceType": "desktop",
            "country":    "us",
            "pageName":   "search-results",
            "ddoKey":     "eagerLoadRefineSearch",
            "pageId":     "page11",
            "siteType":   "external",
            "keywords":   "",
            "subsearch":  "",
            "from":       from_offset,
            "size":       PAGE_SIZE,
            "sortBy":     "",
            "eid":        eid or "",
            "jobs":       True,
            "counts":     True,
            "global":     True,
            "clearAll":   False,
            "isSliderEnable": True,
            "jdsource":   "facets",
            "ak":         "",
            "s":          str(page_num),
            "all_fields": ["category", "country", "state", "city",
                           "postalCode", "jobType", "phLocSlider"],
            "locationData": {
                "sliderRadius":   302,
                "aboveMaxRadius": True,
                "LocationUnit":   "kilometers",
            },
            "selected_fields": {"country": ["India"]},
        }

    def _extract_req_id(self, apply_url: str) -> str:
        try:
            path = apply_url.rstrip("/")
            if path.endswith("/apply"):
                path = path[:-6]
            last_seg = path.split("/")[-1]
            req_id   = last_seg.split("_")[-1]
            return req_id if req_id.startswith("R-") else ""
        except Exception:
            return ""

    def _fetch_detail(self, req_id: str, slug: str, browser, wait) -> dict:
        empty = {"overview": "", "role": "", "all_about_you": "", "qualifications": ""}
        if not req_id or browser is None:
            return empty
        try:
            browser.get(f"{JOB_DETAIL_BASE}/{req_id}/{slug}")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.jd-info")))
            except TimeoutException:
                print(f"    [!] Timeout div.jd-info for {req_id}")
                return empty
            soup   = BeautifulSoup(browser.page_source, "html.parser")
            jd_div = soup.find("div", class_="jd-info")
            if not jd_div:
                return empty
            return self._parse_jd(str(jd_div))
        except Exception as e:
            print(f"    [!] MC detail {req_id}: {e}")
            return empty

    def _parse_jd(self, html: str) -> dict:
        result = {"overview": "", "role": "", "all_about_you": "", "qualifications": ""}
        soup   = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(attrs={"aria-hidden": "true"}):
            tag.decompose()

        current_key   = "overview"
        current_lines = []

        def flush(key, lines):
            if key is None: return
            text = "\n".join(l for l in lines if l.strip())
            if text:
                result[key] = (result[key] + "\n" + text).strip()

        for line in soup.get_text(separator="\n").split("\n"):
            line = line.strip()
            if not line: continue
            lower = line.lower().rstrip(":").strip()
            if lower in ("our purpose", "title and summary"): continue
            matched = False
            for keyword, field in SECTION_MAP.items():
                if lower == keyword or lower.startswith(keyword):
                    flush(current_key, current_lines)
                    current_key   = field
                    current_lines = []
                    matched       = True
                    break
            if not matched:
                current_lines.append(line)

        flush(current_key, current_lines)
        return result
