"""scrapers/visa.py — Visa (SmartRecruiters public REST API)

CRITICAL notes carried from architecture doc:
- city= filter is exact-match; "Bangalore" misses "Bengaluru". Pull all country=in, filter in-memory.
- jobAd.sections is a DICT, not a list. Never iterate — access by key directly.
- Detail calls parallelised with ThreadPoolExecutor.
"""

import re
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY

DETAIL_WORKERS   = 8
SR_BASE          = "https://api.smartrecruiters.com/v1/companies/visa/postings"
APPLY_BASE       = "https://www.visa.co.in/en_in/jobs"
PAGE_SIZE        = 100
TARGET_CITIES    = ["bangalore", "bengaluru", "mumbai", "bombay"]

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.visa.co.in/",
}


class VisaScraper(BaseScraper):
    company_name = "Visa"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Visa  |  India → Bangalore + Mumbai (SmartRecruiters API)")
        print(f"  Pull all country=in, filter in-memory | {DETAIL_WORKERS} workers")
        print("=" * 60)

        session     = requests.Session()
        session.headers.update(HEADERS)
        offset      = 0
        total_found = None
        filtered_out = 0
        new_stubs   = []
        skipped     = 0

        # ── Pass 1: collect all stubs ──────────────────────────────────────────
        while True:
            url = f"{SR_BASE}?country=in&limit={PAGE_SIZE}&offset={offset}"
            print(f"\n[*] offset={offset}" + (f"/{total_found}" if total_found else ""))
            try:
                resp = session.get(url, timeout=20)
                if not resp.ok:
                    print(f"[!] HTTP {resp.status_code}"); break
                data    = resp.json()
                content = data.get("content") or []
                if total_found is None:
                    total_found = int(data.get("totalFound", 0) or 0)
                    print(f"[*] totalFound={total_found}")
            except Exception as e:
                print(f"[!] {e}"); break

            if not content:
                print("[*] Empty page — done."); break

            for item in content:
                posting_id = str(item.get("id") or item.get("uuid") or "").strip()
                ref_number = (item.get("refNumber") or "").strip()
                title      = (item.get("name") or "").strip()
                if not posting_id or not title: continue

                loc_obj = item.get("location") or {}
                city    = (loc_obj.get("city") or "").lower()
                if not any(kw in city for kw in TARGET_CITIES):
                    filtered_out += 1; continue

                apply_url = f"{APPLY_BASE}/{ref_number}" if ref_number else f"{APPLY_BASE}/{posting_id}"
                if apply_url in db:
                    skipped += 1; continue

                loc_parts = [loc_obj.get("city",""), loc_obj.get("region",""), loc_obj.get("country","")]
                stub = {
                    "company":            "Visa",
                    "fetched_date":       TODAY,
                    "title":              title,
                    "ref_number":         ref_number,
                    "posting_id":         posting_id,
                    "department":         (item.get("department") or {}).get("label",""),
                    "job_function":       (item.get("function") or {}).get("label",""),
                    "experience_level":   (item.get("experienceLevel") or {}).get("label",""),
                    "type_of_employment": (item.get("typeOfEmployment") or {}).get("label",""),
                    "locations":          ", ".join(p for p in loc_parts if p),
                    "posted_date":        (item.get("releasedDate") or "")[:10],
                    "apply_url":          apply_url,
                    "job_description":    "",
                    "qualifications":     "",
                }
                new_stubs.append(stub)

            print(f"[*] {len(content)} fetched | {len(new_stubs)} new stubs | {filtered_out} filtered | {skipped} skipped")
            offset += PAGE_SIZE
            if total_found is not None and offset >= total_found: break
            if len(content) < PAGE_SIZE: break
            time.sleep(0.4)

        print(f"\n[*] {len(new_stubs)} new jobs — parallel detail fetch...")
        scraped = 0
        lock    = threading.Lock()

        def fetch_and_insert(stub: dict):
            nonlocal scraped
            detail = self._fetch_detail(session, stub["posting_id"])
            stub.update(detail)
            stub.pop("posting_id", None)
            if upsert_job(db, stub):
                with lock:
                    scraped += 1
                    if scraped % 20 == 0: save_db(db)
                print(f"  ✓ {stub['title']} | {stub['locations']}")

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            for future in as_completed([pool.submit(fetch_and_insert, s) for s in new_stubs]):
                try: future.result()
                except Exception as e: print(f"  [!] {e}")

        save_db(db)
        append_log("Visa", scraped)
        print(f"\n  Visa → Scraped: {scraped} | Skipped: {skipped} | Filtered: {filtered_out}")
        return scraped

    def _fetch_detail(self, session: requests.Session, posting_id: str) -> dict:
        empty = {"job_description": "", "qualifications": ""}
        try:
            resp = session.get(f"{SR_BASE}/{posting_id}", timeout=15)
            if not resp.ok: return empty
            sections = (resp.json().get("jobAd") or {}).get("sections") or {}
            if not isinstance(sections, dict): return empty

            def extract(html):
                if not html: return ""
                soup = BeautifulSoup(html, "html.parser")
                lis  = [li.get_text().strip() for li in soup.find_all("li") if li.get_text().strip()]
                return "\n".join(f"• {l}" for l in lis) if lis else re.sub(r"\s+", " ", soup.get_text(separator="\n")).strip()

            return {
                "job_description": extract((sections.get("jobDescription") or {}).get("text","")),
                "qualifications":  extract((sections.get("qualifications") or {}).get("text","")),
            }
        except Exception as e:
            print(f"    [!] Visa detail {posting_id}: {e}")
            return empty
