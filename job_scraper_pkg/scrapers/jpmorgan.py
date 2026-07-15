"""scrapers/jpmorgan.py — JPMorgan Chase (Oracle HCM REST API)

Detail fetching is parallelised with ThreadPoolExecutor:
  - Collect all new job stubs from listing pages (sequential — API pages)
  - Fire up to DETAIL_WORKERS concurrent detail calls
  - Each detail call is independent (separate session, separate req_id)
"""

import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY
from ..core.helpers import clean_text

DETAIL_WORKERS = 8   # concurrent detail API calls — Oracle HCM handles this fine

SEARCH_URL = (
    "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    "?onlyData=true"
    "&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
    "&finder=findReqs"
    ";siteNumber=CX_1001"
    ",locationId=300000000289360"
    ",location=India"
    ",lastSelectedFacet=POSTING_DATES"
    ",facetsList=LOCATIONS%3BWORK_LOCATIONS%3BTITLES%3BCATEGORIES%3BPOSTING_DATES%3BFLEX_FIELDS"
    ",sortBy=POSTING_DATES_DESC"
    ",offset={offset}"
    ",limit=25"
)
DETAIL_URL = (
    "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    "?expand=all&onlyData=true&finder=ById"
    ";Id=%22{req_id}%22,siteNumber=CX_1001"
)
JOB_BASE = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{req_id}"
HEADERS  = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs",
}

HEADING_MAP = {
    "job summary":                                        "summary",
    "job responsibilities":                               "responsibilities",
    "required qualifications, capabilities, and skills":  "required_qualifications",
    "preferred qualifications, capabilities, and skills": "preferred_qualifications",
}


class JPMorganScraper(BaseScraper):
    company_name = "JPMorgan Chase"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  JPMorgan Chase  |  India  (Oracle HCM REST API)")
        print(f"  Detail workers: {DETAIL_WORKERS} concurrent")
        print("=" * 60)

        session = requests.Session()
        session.headers.update(HEADERS)
        total_jobs = None
        offset     = 0
        new_stubs  = []  # jobs needing detail fetch
        skipped    = 0

        # ── Pass 1: collect all listing stubs ─────────────────────────────────
        while True:
            url = SEARCH_URL.format(offset=offset)
            print(f"\n[*] Listing offset={offset}" + (f"/{total_jobs}" if total_jobs else ""))
            try:
                resp = session.get(url, timeout=20)
                if not resp.ok:
                    print(f"[!] HTTP {resp.status_code}"); break
                data = resp.json()
            except Exception as e:
                print(f"[!] {e}"); break

            outer  = data.get("items", []) or []
            if not outer:
                break
            obj      = outer[0]
            job_list = obj.get("requisitionList", []) or []
            if total_jobs is None:
                total_jobs = int(obj.get("TotalJobsCount", 0) or 0)
                print(f"[*] TotalJobsCount = {total_jobs}")

            if not job_list:
                break

            for item in job_list:
                stub = self._parse_stub(item)
                if not stub["apply_url"]:
                    continue
                if stub["apply_url"] in db:
                    skipped += 1
                else:
                    new_stubs.append(stub)

            print(f"[*] Page: {len(job_list)} | New so far: {len(new_stubs)} | Skipped: {skipped}")
            offset += 25
            if not job_list or len(job_list) < 25:
                break
            if total_jobs and offset >= total_jobs:
                break
            time.sleep(0.4)

        print(f"\n[*] Listing done — {len(new_stubs)} new jobs to detail-fetch")

        # ── Pass 2: parallel detail fetching ──────────────────────────────────
        scraped   = 0
        lock      = threading.Lock()

        def fetch_and_insert(stub: dict):
            nonlocal scraped
            req_id = stub["job_id"]
            detail = self._fetch_detail(session, req_id)
            stub.update(detail)
            if upsert_job(db, stub):
                with lock:
                    scraped += 1
                    if scraped % 10 == 0:
                        save_db(db)
                print(f"  ✓ [{scraped}] {stub['title']} | {stub['locations']}")

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            futures = [pool.submit(fetch_and_insert, stub) for stub in new_stubs]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"  [!] Detail error: {e}")

        save_db(db)
        append_log("JPMorgan Chase", scraped)
        print(f"\n  JPMorgan → Scraped: {scraped} | Skipped: {skipped}")
        return scraped

    def _parse_stub(self, item: dict) -> dict:
        req_id   = str(item.get("Id") or "")
        primary  = item.get("PrimaryLocation", "") or ""
        sec_list = item.get("secondaryLocations") or []
        sec_locs = [s.get("Name", "") for s in sec_list if isinstance(s, dict) and s.get("Name")]
        locations = " | ".join(dict.fromkeys(l for l in [primary] + sec_locs if l))
        return {
            "company":                  "JPMorgan Chase",
            "fetched_date":             TODAY,
            "title":                    (item.get("Title") or "").strip(),
            "job_family":               (item.get("JobFamily") or "").strip(),
            "job_function":             (item.get("JobFunction") or "").strip(),
            "locations":                locations,
            "job_id":                   req_id,
            "posted_date":              (item.get("PostedDate") or "").strip(),
            "apply_url":                JOB_BASE.format(req_id=req_id) if req_id else "",
            "summary": "", "description": "", "responsibilities": "",
            "required_qualifications": "", "preferred_qualifications": "",
        }

    def _fetch_detail(self, session: requests.Session, req_id: str) -> dict:
        empty = {"summary":"","description":"","responsibilities":"",
                 "required_qualifications":"","preferred_qualifications":""}
        try:
            resp = session.get(DETAIL_URL.format(req_id=req_id), timeout=15)
            if not resp.ok:
                return empty
            items = resp.json().get("items", [])
            if not items:
                return empty
            item   = items[0]
            parsed = self._parse_desc_html(item.get("ExternalDescriptionStr", ""))
            short  = (item.get("ShortDescriptionStr") or "").strip()
            if short:
                parsed["summary"] = short
            return parsed
        except Exception as e:
            print(f"    [!] Detail {req_id}: {e}")
            return empty

    def _parse_desc_html(self, html: str) -> dict:
        result   = {"description":"","summary":"","responsibilities":"",
                    "required_qualifications":"","preferred_qualifications":""}
        if not html:
            return result
        soup     = BeautifulSoup(html, "html.parser")
        sections = {k: [] for k in result}
        current  = "description"
        for tag in soup.find_all(["strong","li","p","div"]):
            if tag.name == "strong":
                heading = tag.get_text(strip=True).lower().rstrip(":")
                current = HEADING_MAP.get(heading, current)
            elif tag.name == "li":
                text = tag.get_text(strip=True)
                if text: sections[current].append(f"• {text}")
            elif tag.name in ("p","div"):
                direct = "".join(str(c) for c in tag.children if not hasattr(c,"name")).strip()
                direct = BeautifulSoup(direct,"html.parser").get_text(strip=True)
                if direct and not any(h in direct.lower() for h in HEADING_MAP):
                    sections[current].append(direct)
        for key, lines in sections.items():
            seen = set(); deduped = []
            for l in lines:
                if l not in seen: seen.add(l); deduped.append(l)
            result[key] = "\n".join(deduped)
        return result
