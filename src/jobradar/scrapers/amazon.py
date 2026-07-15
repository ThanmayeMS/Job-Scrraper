"""Amazon Jobs — public JSON API. Reference implementation of the API-scraper pattern.

Pure requests: the search.json endpoint returns full JD content in the listing, so no
separate detail call is needed. Ported from the original project to write via
`JobRepository` instead of a JSON dict.
"""

import logging
import time

import requests
from bs4 import BeautifulSoup

from jobradar.scrapers.base import BaseScraper
from jobradar.scrapers.helpers import clean_html
from jobradar.scrapers.repository import JobRepository

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.amazon.jobs/en/search.json"
    "?offset={offset}&result_limit=10&sort=relevant"
    "&job_type[]=Full-Time&category_type=Corporate"
    "&loc_query=India&country=IND"
    "&latitude=28.63141&longitude=77.21676&radius=24km"
)
JOB_BASE = "https://www.amazon.jobs"
HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://www.amazon.jobs/en/search",
}


class AmazonScraper(BaseScraper):
    company_name = "Amazon"

    def scrape(self, repo: JobRepository) -> int:
        session = requests.Session()
        session.headers.update(HEADERS)
        scraped = skipped = offset = 0

        while True:
            try:
                resp = session.get(SEARCH_URL.format(offset=offset), timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("Amazon request failed at offset %d: %s", offset, exc)
                break

            jobs_raw = data.get("jobs", [])
            total_hits = data.get("hits", 0)
            if not jobs_raw:
                break

            for raw in jobs_raw:
                job = self._parse(raw)
                if not job["apply_url"]:
                    continue
                if repo.upsert(job):
                    scraped += 1
                else:
                    skipped += 1

            offset += 10
            if total_hits and offset >= total_hits:
                break
            time.sleep(0.5)

        repo.log_run("Amazon", scraped)
        log.info("Amazon done — %d new, %d skipped", scraped, skipped)
        return scraped

    def _parse(self, j: dict) -> dict:
        job_id = j.get("id_icims", "") or j.get("id", "")
        url_path = j.get("job_path", "") or f"/en/jobs/{job_id}"
        raw_locs = j.get("locations", []) or []
        if isinstance(raw_locs, str):
            raw_locs = [raw_locs]
        locations = " | ".join(
            loc.get("city", str(loc)) if isinstance(loc, dict) else str(loc) for loc in raw_locs
        )
        desc = j.get("description", "") or ""
        if desc:
            desc = BeautifulSoup(desc, "html.parser").get_text(separator="\n").strip()
        org = j.get("business_category", {})
        org = org.get("display_name", "") if isinstance(org, dict) else str(org or "")
        team = j.get("team", {})
        team = team.get("label", "") if isinstance(team, dict) else ""
        return {
            "company": "Amazon",
            "title": (j.get("title") or "").strip(),
            "organization": org,
            "team": team,
            "locations": locations,
            "experience_level": j.get("job_family", "") or j.get("level", ""),
            "job_id": str(job_id),
            "posted_date": j.get("posted_date", "") or j.get("updated_time", ""),
            "apply_url": JOB_BASE + url_path if url_path else "",
            "about_the_job": desc,
            "minimum_qualifications": clean_html(j.get("basic_qualifications", "")),
            "preferred_qualifications": clean_html(j.get("preferred_qualifications", "")),
            "responsibilities": clean_html(j.get("responsibilities", "")),
        }
