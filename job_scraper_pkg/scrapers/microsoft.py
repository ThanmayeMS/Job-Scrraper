"""scrapers/microsoft.py — Microsoft (Eightfold.ai ATS — public JSON API)

CRITICAL notes:
- Use /api/pcsx/position_details, NOT /overrides (200 OK but empty — silent failure)
- Drop pid= from listing URL — it limits to one profession category
- No total count returned — stop on empty positions list
- postedTs is Unix timestamp in seconds
"""

import re
import time
import threading
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY

DETAIL_WORKERS = 8
SEARCH_URL  = "https://apply.careers.microsoft.com/api/pcsx/search"
DETAIL_URL  = "https://apply.careers.microsoft.com/api/pcsx/position_details"
LOCATION    = "India, Multiple Locations, Multiple Locations"
PAGE_SIZE   = 10

HEADERS = {
    "Accept":     "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://apply.careers.microsoft.com/",
    "Origin":     "https://apply.careers.microsoft.com",
}


class MicrosoftScraper(BaseScraper):
    company_name = "Microsoft"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Microsoft  |  India — Eightfold.ai API")
        print(f"  Pure requests | {DETAIL_WORKERS} concurrent detail workers")
        print("=" * 60)

        session   = requests.Session()
        session.headers.update(HEADERS)
        start     = 0
        new_stubs = []
        skipped   = 0

        # ── Pass 1: collect all listing stubs ─────────────────────────────────
        while True:
            print(f"\n[*] start={start}")
            try:
                resp = session.get(SEARCH_URL, params={
                    "domain": "microsoft.com", "query": "",
                    "location": LOCATION, "start": start,
                    "sort_by": "date", "filter_include_remote": 1,
                }, timeout=20)
                if not resp.ok:
                    print(f"[!] HTTP {resp.status_code}"); break
                positions = resp.json().get("data", {}).get("positions") or []
            except Exception as e:
                print(f"[!] {e}"); break

            if not positions:
                print("[*] Empty page — done."); break

            for pos in positions:
                pos_id = pos.get("id")
                title  = (pos.get("name") or "").strip()
                if not pos_id or not title: continue
                apply_url = f"https://apply.careers.microsoft.com/careers/job/{pos_id}"
                if apply_url in db:
                    skipped += 1; continue
                locs    = pos.get("locations") or []
                ts      = pos.get("postedTs")
                stub    = {
                    "company":              "Microsoft",
                    "fetched_date":         TODAY,
                    "title":                title,
                    "display_job_id":       (pos.get("displayJobId") or str(pos_id)).strip(),
                    "department":           (pos.get("department") or "").strip(),
                    "locations":            "; ".join(locs) if locs else "",
                    "work_location_option": (pos.get("workLocationOption") or "").strip(),
                    "posted_date":          self._ts(ts) if ts else "",
                    "apply_url":            apply_url,
                    "_pos_id":              pos_id,   # temp field for detail call
                    "overview":"","responsibilities":"",
                    "minimum_qualifications":"","preferred_qualifications":"",
                }
                new_stubs.append(stub)

            print(f"[*] {len(positions)} positions | {len(new_stubs)} new | {skipped} skipped")
            start += PAGE_SIZE
            time.sleep(0.3)

        print(f"\n[*] {len(new_stubs)} new jobs — parallel detail fetch...")
        scraped = 0
        lock    = threading.Lock()

        def fetch_and_insert(stub: dict):
            nonlocal scraped
            pos_id = stub.pop("_pos_id", None)
            detail = self._fetch_detail(session, pos_id)
            stub.update(detail)
            if upsert_job(db, stub):
                with lock:
                    scraped += 1
                    if scraped % 10 == 0: save_db(db)
                print(f"  ✓ {stub['title']} | {stub['locations']}")

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            for future in as_completed([pool.submit(fetch_and_insert, s) for s in new_stubs]):
                try: future.result()
                except Exception as e: print(f"  [!] {e}")

        save_db(db)
        append_log("Microsoft", scraped)
        print(f"\n  Microsoft → Scraped: {scraped} | Skipped: {skipped}")
        return scraped

    def _ts(self, ts) -> str:
        try: return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except: return ""

    def _fetch_detail(self, session: requests.Session, pos_id) -> dict:
        empty = {"overview":"","responsibilities":"","minimum_qualifications":"","preferred_qualifications":""}
        try:
            resp = session.get(DETAIL_URL, params={
                "position_id": str(pos_id), "domain": "microsoft.com",
                "hl": "en", "queried_location": LOCATION,
            }, timeout=20)
            if not resp.ok: return empty
            html = resp.json().get("data", {}).get("jobDescription") or ""
            return self._parse_jd(html)
        except Exception as e:
            print(f"    [!] MSFT detail {pos_id}: {e}")
            return empty

    def _parse_jd(self, html: str) -> dict:
        result = {"overview":"","responsibilities":"","minimum_qualifications":"","preferred_qualifications":""}
        if not html: return result
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(attrs={"aria-hidden": True}): tag.decompose()
        HMAP = {"overview":"overview","responsibilities":"responsibilities","qualifications":"qualifications"}
        sections: dict = {k: [] for k in list(HMAP.values()) + ["qualifications"]}
        active = None
        for tag in soup.find_all(["b","p","li"]):
            if tag.name == "b":
                h = re.sub(r"\s+", " ", tag.get_text()).strip().lower().rstrip(":")
                if h in HMAP: active = HMAP[h]
                continue
            if active is None: continue
            if tag.name == "li":
                text = re.sub(r"\s+", " ", tag.get_text()).strip()
                if text: sections[active].append(f"• {text}")
            elif tag.name == "p":
                text = re.sub(r"\s+", " ", tag.get_text()).strip()
                if text: sections[active].append(text)
        result["overview"]         = "\n".join(sections["overview"])
        result["responsibilities"] = "\n".join(sections["responsibilities"])
        qual = "\n".join(sections["qualifications"])
        parts = re.split(r"(?i)preferred qualifications\s*:?", qual, maxsplit=1)
        result["minimum_qualifications"]   = parts[0].strip()
        result["preferred_qualifications"] = parts[1].strip() if len(parts) > 1 else ""
        return result
