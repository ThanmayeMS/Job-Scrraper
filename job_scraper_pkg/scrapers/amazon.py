"""scrapers/amazon.py — Amazon Jobs (public JSON API)"""

import time
import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY
from ..core.helpers import clean_html

SEARCH_URL = (
    "https://www.amazon.jobs/en/search.json"
    "?offset={offset}&result_limit=10&sort=relevant"
    "&job_type[]=Full-Time&category_type=Corporate"
    "&loc_query=India&country=IND"
    "&latitude=28.63141&longitude=77.21676&radius=24km"
)
JOB_BASE = "https://www.amazon.jobs"
HEADERS  = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.amazon.jobs/en/search",
}


class AmazonScraper(BaseScraper):
    company_name = "Amazon"

    def scrape(self, db: dict) -> int:
        print("\n" + "=" * 60)
        print("  Amazon Jobs  |  India · Full-Time · Corporate")
        print("=" * 60)

        session = requests.Session()
        session.headers.update(HEADERS)
        scraped = skipped = offset = 0

        while True:
            print(f"\n[*] Offset {offset}")
            try:
                resp = session.get(SEARCH_URL.format(offset=offset), timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[!] Request failed: {e}")
                break

            jobs_raw   = data.get("jobs", [])
            total_hits = data.get("hits", 0)
            if not jobs_raw:
                print("[*] Empty page — done.")
                break

            new_n = skip_n = 0
            for raw in jobs_raw:
                job = self._parse(raw)
                if not job["apply_url"]:
                    continue
                if upsert_job(db, job):
                    new_n += 1; scraped += 1
                    print(f"  ✓ {job['title']} | {job['locations']}")
                else:
                    skip_n += 1; skipped += 1

            print(f"[*] {new_n} new | {skip_n} skipped | total: {total_hits}")
            if new_n > 0:
                save_db(db)

            offset += 10
            if total_hits and offset >= total_hits:
                break
            time.sleep(0.5)

        save_db(db)
        append_log("Amazon", scraped)
        print(f"\n  Amazon → Scraped: {scraped} | Skipped: {skipped}")
        return scraped

    def _parse(self, j: dict) -> dict:
        job_id   = j.get("id_icims", "") or j.get("id", "")
        url_path = j.get("job_path", "") or f"/en/jobs/{job_id}"
        raw_locs = j.get("locations", []) or []
        if isinstance(raw_locs, str):
            raw_locs = [raw_locs]
        locations = " | ".join(
            loc.get("city", str(loc)) if isinstance(loc, dict) else str(loc)
            for loc in raw_locs
        )
        desc = j.get("description", "") or ""
        if desc:
            desc = BeautifulSoup(desc, "html.parser").get_text(separator="\n").strip()
        org  = j.get("business_category", {})
        org  = org.get("display_name", "") if isinstance(org, dict) else str(org or "")
        team = j.get("team", {})
        team = team.get("label", "") if isinstance(team, dict) else ""
        return {
            "company":                  "Amazon",
            "fetched_date":             TODAY,
            "title":                    (j.get("title") or "").strip(),
            "organization":             org,
            "team":                     team,
            "locations":                locations,
            "experience_level":         j.get("job_family", "") or j.get("level", ""),
            "job_id":                   str(job_id),
            "posted_date":              j.get("posted_date", "") or j.get("updated_time", ""),
            "apply_url":                JOB_BASE + url_path if url_path else "",
            "about_the_job":            desc,
            "minimum_qualifications":   clean_html(j.get("basic_qualifications", "")),
            "preferred_qualifications": clean_html(j.get("preferred_qualifications", "")),
            "responsibilities":         clean_html(j.get("responsibilities", "")),
        }
