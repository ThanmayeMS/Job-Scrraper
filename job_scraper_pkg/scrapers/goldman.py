"""scrapers/goldman.py — Goldman Sachs (GraphQL listing + Next.js SSG detail)"""

import re
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY

DETAIL_WORKERS = 8

GRAPHQL_URL = "https://api-higher.gs.com/gateway/api/v1/graphql"
HOME_URL    = "https://higher.gs.com"
DETAIL_URL  = "https://higher.gs.com/_next/data/{build_id}/roles/{source_id}.json?roleId={source_id}"
ROLE_URL    = "https://higher.gs.com/roles/{source_id}"

HEADERS = {
    "Content-Type": "application/json", "Accept": "*/*",
    "Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
}

GRAPHQL_QUERY = """
query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId corporateTitle jobTitle jobFunction
      locations { primary state country city __typename }
      status division skills
      jobType { code description __typename }
      externalSource { sourceId __typename }
      __typename
    }
    __typename
  }
}
"""

LOCATION_FILTER = {
    "filterCategoryType": "LOCATION",
    "filters": [{"filter": "India", "subFilters": [
        {"filter": "Karnataka",   "subFilters": [{"filter": "Bengaluru",  "subFilters": []}]},
        {"filter": "Maharashtra", "subFilters": [{"filter": "Mumbai",     "subFilters": []}]},
        {"filter": "Telangana",   "subFilters": [{"filter": "Hyderabad",  "subFilters": []}]},
    ]}]
}

HEADING_MAP = {
    "primary responsibilities":         "responsibilities",
    "basic qualifications":             "basic_qualifications",
    "preferred qualifications":         "preferred_qualifications",
    "our impact":                       "our_impact",
    "your impact":                      "your_impact",
    "job summary and responsibilities": "responsibilities",
    "job summary":                      "responsibilities",
    "responsibilities":                 "responsibilities",
    "qualifications":                   "basic_qualifications",
}
BOILERPLATE = {"what we do","who we are","goldman sachs engineering culture","about goldman sachs"}
SKIP_PHRASES = ["Goldman Sachs Group, Inc.","equal employment","reasonable accommodations",
                "goldmansachs.com/careers","gs.com/careers","We're committed to finding","© Goldman Sachs"]


class GoldmanScraper(BaseScraper):
    company_name = "Goldman Sachs"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Goldman Sachs  |  Bengaluru · Hyderabad · Mumbai")
        print(f"  GraphQL listing + Next.js SSG detail | {DETAIL_WORKERS} workers")
        print("=" * 60)

        session     = requests.Session()
        session.headers.update(HEADERS)
        build_id    = self._fetch_build_id(session)
        total_count = None
        page_number = 0
        page_size   = 20
        new_stubs   = []
        skipped     = 0

        while True:
            payload = {
                "operationName": "GetRoles",
                "variables": {"searchQueryInput": {
                    "page": {"pageSize": page_size, "pageNumber": page_number},
                    "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                    "filters": [LOCATION_FILTER],
                    "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
                    "searchTerm": "",
                }},
                "query": GRAPHQL_QUERY,
            }
            try:
                resp = session.post(GRAPHQL_URL, json=payload, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[!] {e}"); break

            rs = data.get("data", {}).get("roleSearch", {}) or {}
            if total_count is None:
                total_count = int(rs.get("totalCount", 0) or 0)
                print(f"[*] totalCount={total_count}")

            items = rs.get("items", []) or []
            print(f"[*] Page {page_number}: {len(items)} items")
            if not items:
                break

            for item in items:
                source_id = (item.get("externalSource") or {}).get("sourceId", "")
                if not source_id:
                    continue
                role_url = ROLE_URL.format(source_id=source_id)
                if role_url in db:
                    skipped += 1; continue
                locs = item.get("locations") or []
                loc_parts = [l.get("city","") or l.get("state","") or l.get("country","")
                             for l in locs if isinstance(l, dict)]
                stub = {
                    "company": "Goldman Sachs", "fetched_date": TODAY,
                    "title":           (item.get("jobTitle") or "").strip(),
                    "corporate_title": (item.get("corporateTitle") or "").strip(),
                    "job_function":    (item.get("jobFunction") or "").strip(),
                    "division":        (item.get("division") or "").strip(),
                    "locations":       " | ".join(filter(None, loc_parts)),
                    "source_id":       source_id,
                    "apply_url":       role_url,
                    "description":"","our_impact":"","your_impact":"",
                    "responsibilities":"","basic_qualifications":"","preferred_qualifications":"",
                }
                new_stubs.append(stub)

            page_number += 1
            if total_count and page_number * page_size >= total_count:
                break
            time.sleep(0.4)

        print(f"\n[*] {len(new_stubs)} new stubs, {skipped} skipped — fetching details...")
        scraped = 0
        lock    = threading.Lock()

        def fetch_and_insert(stub: dict):
            nonlocal scraped
            source_id = stub["source_id"]
            detail    = self._fetch_detail(session, source_id, build_id)
            ext_url   = detail.pop("apply_url_external", "")
            if ext_url:
                if ext_url in db:
                    return
                stub["apply_url"]        = ext_url
                stub["apply_url_browse"] = ROLE_URL.format(source_id=source_id)
            stub.update({k: v for k, v in detail.items() if v})
            if upsert_job(db, stub):
                with lock:
                    scraped += 1
                    if scraped % 10 == 0:
                        save_db(db)
                print(f"  ✓ {stub['title']} | {stub['locations']}")

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            for future in as_completed([pool.submit(fetch_and_insert, s) for s in new_stubs]):
                try: future.result()
                except Exception as e: print(f"  [!] {e}")

        save_db(db)
        append_log("Goldman Sachs", scraped)
        print(f"\n  Goldman → Scraped: {scraped} | Skipped: {skipped}")
        return scraped

    def _fetch_build_id(self, session: requests.Session) -> str:
        try:
            resp = session.get(HOME_URL, timeout=15)
            if resp.ok:
                m = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
                if m: return m.group(1)
        except Exception: pass
        print("  [!] GS buildId not found — detail fetch disabled")
        return ""

    def _fetch_detail(self, session: requests.Session, source_id: str, build_id: str) -> dict:
        empty = {"description":"","our_impact":"","your_impact":"",
                 "responsibilities":"","basic_qualifications":"","preferred_qualifications":"",
                 "apply_url_external":""}
        if not build_id or not source_id:
            return empty
        try:
            url  = DETAIL_URL.format(build_id=build_id, source_id=source_id)
            resp = session.get(url, timeout=15)
            if not resp.ok: return empty
            role = resp.json().get("pageProps", {}).get("role", {}) or {}
            parsed = self._parse_desc_html(role.get("descriptionHtml", ""))
            ext    = (role.get("externalSource") or {}).get("externalApplicationUrl", "")
            parsed["apply_url_external"] = ext
            return parsed
        except Exception as e:
            print(f"    [!] GS detail {source_id}: {e}")
            return empty

    def _parse_desc_html(self, html: str) -> dict:
        result = {k: "" for k in ["description","our_impact","your_impact",
                                   "responsibilities","basic_qualifications","preferred_qualifications"]}
        if not html: return result
        soup = BeautifulSoup(html, "html.parser")
        sections: dict = {k: [] for k in result}
        current = "description"
        for tag in soup.find_all(["strong","p","li"]):
            if tag.name == "strong":
                h = tag.get_text(strip=True).lower().rstrip(":").strip()
                if h in BOILERPLATE: current = "description"
                else:
                    matched = None
                    for k, v in HEADING_MAP.items():
                        if k in h and (matched is None or len(k) > len(matched[0])):
                            matched = (k, v)
                    current = matched[1] if matched else "description"
                continue
            if tag.name == "li":
                text = tag.get_text(strip=True)
                if text: sections[current].append(f"• {text}")
            elif tag.name == "p":
                strong = tag.find("strong")
                if strong and not tag.get_text(strip=True).replace(strong.get_text(strip=True),"").strip():
                    continue
                text = tag.get_text(separator=" ", strip=True)
                if text and len(text) > 15 and not any(s in text for s in SKIP_PHRASES):
                    sections[current].append(text)
        for key, lines in sections.items():
            seen = set(); deduped = []
            for l in lines:
                if l not in seen: seen.add(l); deduped.append(l)
            result[key] = "\n".join(deduped)
        return result
