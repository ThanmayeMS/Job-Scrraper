# JobRadar — Architecture & Best Practices

**Full pipeline context document: scraper → scorer → viewer.**
When adding a new company, read the relevant platform section first to avoid re-learning known pitfalls.

---

## System Overview

```
job_scraper_pkg/          ← scraper (plugin architecture)
  __main__.py             ← runner: python -m job_scraper_pkg
  base.py                 ← BaseScraper contract
  core/
    db.py                 ← thread-safe DB helpers (load, save, upsert, log)
    browser.py            ← shared Selenium factory
    helpers.py            ← shared HTML/text utilities
  scrapers/
    __init__.py           ← SCRAPER_REGISTRY dict
    google.py             ← Selenium
    amazon.py             ← pure requests
    jpmorgan.py           ← pure requests + concurrent detail
    goldman.py            ← pure requests + concurrent detail
    mastercard.py         ← requests listing + Selenium detail
    visa.py               ← pure requests + concurrent detail
    microsoft.py          ← pure requests + concurrent detail
    citi.py               ← Selenium 2-pass

score_jobs_openai.py      ← scorer (10 concurrent workers, no batching)
jobs_db.json              ← full job database (output)
jobs_daily_log.json       ← daily scrape counts (output)
jobradar_tracker.json     ← bookmarks + applied tracking (viewer output)
job_viewer.html           ← local dashboard (single-file, no server needed)
```

---

## Quick Reference

| Field | Value |
|---|---|
| Output files | `jobs_db.json` (all jobs), `jobs_daily_log.json` (daily counts per company) |
| Key field | `fetched_date` (yyyy-mm-dd) — set on first insert, never overwritten |
| Dedup key | `apply_url` — unique per job, used as dict key in memory and JSON |
| Run control | `COMPANIES_TO_RUN` list in `__main__.py` |
| Stack | Python 3, Selenium (JS-heavy sites), requests (API sites), BeautifulSoup |
| Concurrency | `ThreadPoolExecutor` for API detail calls (8 workers per scraper) |
| Scorer model | `gpt-4o-mini`, 10 concurrent workers, no batching, no sleep |

**JSON schema is intentionally different per company** — do not overlay the same fields across all companies. Each company section below defines its own field set based on what the site actually provides.

---

## Running the System

### Scraper

```bash
# Run all companies sequentially (default)
python -m job_scraper_pkg

# Run API scrapers concurrently, then Selenium scrapers sequentially
python -m job_scraper_pkg --parallel

# Run specific companies only
python -m job_scraper_pkg --companies amazon jpmorgan visa

# List all registered scrapers
python -m job_scraper_pkg --list
```

### Scorer

```bash
# Score all unscored jobs (10 concurrent workers)
python score_jobs_openai.py

# Score next N unscored jobs
python score_jobs_openai.py --batch 300

# Show progress, no scoring
python score_jobs_openai.py --status

# Test on 5 jobs
python score_jobs_openai.py --dry-run

# Override concurrency (default 10, safe up to ~30 on Tier 2)
python score_jobs_openai.py --workers 20

# Print top matches
python score_jobs_openai.py --top 20 --min 7

# Re-score everything
python score_jobs_openai.py --rescore
```

### Viewer

Open `job_viewer.html` by double-clicking. Drop `jobs_db.json` and `jobradar_tracker.json` together onto the drop zone. Tracker persists bookmarks and applied status across sessions — keep it, never overwrite it.

---

## Plugin Architecture

### Adding a New Company — 3 Steps

**Step 1:** Create `job_scraper_pkg/scrapers/newcompany.py`:
```python
from ..base import BaseScraper
from ..core.db import upsert_job, save_db, append_log, TODAY

class NewCompanyScraper(BaseScraper):
    company_name = "New Company"

    def scrape(self, db: dict) -> int:
        scraped = 0
        # ... your logic ...
        # call upsert_job(db, job) for each new job
        # call save_db(db) periodically for crash safety
        save_db(db)
        append_log("New Company", scraped)
        return scraped
```

**Step 2:** Register in `scrapers/__init__.py`:
```python
from .newcompany import NewCompanyScraper

SCRAPER_REGISTRY: dict = {
    ...
    "newcompany": NewCompanyScraper,
}
```

**Step 3:** Add to `COMPANIES_TO_RUN` and correct type set in `__main__.py`:
```python
COMPANIES_TO_RUN = [..., "newcompany"]
API_SCRAPERS     = {..., "newcompany"}   # or SELENIUM_SCRAPERS
```

That's it. The runner, DB layer, thread safety, and logging are all inherited.

---

## Concurrency Architecture

### Thread Safety

`core/db.py` owns a module-level `_db_lock = threading.Lock()`. Every call to `upsert_job()` and `save_db()` acquires this lock before touching the dict or writing to disk. Multiple threads can call these simultaneously — only one writes at a time. No data loss, no corruption.

### API Scraper Pattern (2-pass + ThreadPoolExecutor)

API scrapers (JPMorgan, Goldman, Visa, Microsoft) follow this pattern:

```
Pass 1 (sequential): walk all listing pages, collect new job stubs
Pass 2 (concurrent): ThreadPoolExecutor(max_workers=8) fires detail calls in parallel
```

Why 2-pass: listing pagination must be sequential (each page depends on the previous offset). Detail calls are fully independent — each is a separate HTTP request with no shared state.

### Selenium Scraper Pattern

Selenium scrapers (Google, Mastercard, Citi) are always sequential. One browser instance, one page at a time.

### --parallel Flag

```
API scrapers (amazon, jpmorgan, goldman, visa, microsoft)
  → ThreadPoolExecutor, all run simultaneously
  → each has its own requests.Session — no shared state

Selenium scrapers (google, mastercard, citi)
  → run sequentially after API scrapers finish
  → each opens its own browser, closes it when done
```

---

## Platform Identification Guide

Before writing any code for a new company, identify the platform first.

| Signal | Platform | Strategy |
|---|---|---|
| Site loads blank with requests/BS4 | JS-rendered — check Network tab for XHR first | Selenium if no API found |
| Network tab shows XHR to `*.json` | Has a public API underneath | Pure requests |
| `oraclecloud.com` in URL | Oracle HCM | REST API — see JPMorgan section |
| `myworkdayjobs.com` in apply links | Workday ATS | POST to `/wday/cxs/{tenant}/jobs` |
| `boards.greenhouse.io` | Greenhouse | Public API — no auth needed |
| `api.lever.co` | Lever | Public flat JSON array |
| `phenompeople.com` or `/widgets` XHR | Phenom People (API variant) | POST to `/widgets` — seed cookies first |
| `jobs.*.com` with NO XHR at all | Phenom People (SSR variant) | Full Selenium — see Citi section |
| `REF****` job IDs in URLs | SmartRecruiters | Public REST API — see Visa section |
| `higher.gs.com` / GraphQL | Goldman Sachs custom (Next.js + GraphQL) | See Goldman section |
| `apply.careers.microsoft.com` / Eightfold.ai | Microsoft (Eightfold.ai ATS) | Pure requests — see Microsoft section |

---

## General Rules

### URL Encoding
- **Never** use `requests params={}` for APIs with non-standard delimiters (semicolons, pipes).
- Always build the full URL as a plain Python string and pass to `session.get(url)` directly.
- Oracle HCM finder syntax: `finder=<n>;<key>=<val>,<key>=<val>`. Semicolons separate name from params; commas separate params.

### Pagination Patterns

| Pattern | Notes |
|---|---|
| `&offset=N` / `&page=N` | Most common. Increment until empty results or total reached. |
| Finder variable offset | Oracle HCM only. Offset inside the finder string: `,offset=N,limit=25` |
| `totalFound` / `totalHits` / `TotalJobsCount` | Use the total field from the first response as the stop condition |
| Early-stop | If N consecutive pages are all already in DB, stop. Saves time on daily re-runs. |
| Cookie-gated pagination | Phenom People (API variant): server ignores `from` offset without session cookies. Seed session with GET first. |
| Click-based (Phenom SSR) | Phenom People (SSR variant, e.g. Citi): URL params ignored by server. Must click `a.next`. Stop by detecting cards unchanged after click — `a.next` stays in DOM even on last page. |

### HTML Content Parsing
- Always use BeautifulSoup — never regex on raw HTML.
- For list content: extract all `li.get_text()` and prefix with `•`.
- For section-delimited blobs: walk tags looking for `strong`/`b`/`h3`/`h4` headings, accumulate lines per section.
- Strip stray whitespace: `re.sub(r'\s+', ' ', text).strip()` after extraction.
- Remove `aria-hidden='true'` elements before parsing — they add noise to `get_text()`.

### Database Design
- Format: single `jobs_db.json` — list of job objects, loaded as dict keyed by `apply_url`.
- Dedup: `apply_url` as primary key. `upsert_job()` is a no-op if key already exists.
- `fetched_date`: set once via `setdefault()` on first insert. Never overwritten on re-run.
- Flush strategy: `upsert_job()` flushes to disk immediately on every insert — crash-safe at the per-job level.
- Daily log: `jobs_daily_log.json` — one entry per company per day, sorted newest-first.
- Thread safety: all writes acquire `_db_lock` before touching dict or disk.

### Scorer Design
- Model: `gpt-4o-mini` — fast, cheap, accurate enough for job fit scoring.
- Concurrency: 10 `ThreadPoolExecutor` workers. Each job gets its own independent API call.
- No batching: each job gets a focused single-job prompt — preserves accuracy.
- No sleep: artificial throttle removed. 429s trigger per-worker exponential backoff (10s, 20s, 30s).
- Fields written: `similarity_score`, `match_reason`, `matching_skills`, `match_gaps`, `info_level`.
- Safe to stop/resume: every completed job is flushed to disk immediately.
- Cost tip: skip scoring jobs with `info_level = Insufficient` — they score poorly and waste tokens.

---

## Companies

---

### Google Careers

**Platform:** Custom JS-rendered site — requires Selenium

| | |
|---|---|
| **URL** | `google.com/about/careers/applications/jobs/results/?location=India&employment_type=FULL_TIME&sort_by=date&target_level=MID&page=N` |
| **Rendering** | JavaScript — Selenium required (BS4 alone returns empty page) |
| **Pagination** | `&page=N`, increment by 1 until no cards found |
| **Detail page** | Must visit each job URL individually to get description |
| **Concurrency** | Sequential (Selenium) |

**Listing selectors:** cards → `ul.spHGqe li` | title → `h3.QJPWVe` | org → `span.RP7SMd > span` | locations → `span.r0wTof` | experience → `span.wVSTAb` | URL → `a[href*='jobs/results/\d+']`

**Detail selectors:** wait for `h2.p1N2lc` | about → `div.aG5W3` | responsibilities → `div.BDNOWe li` | qualifications → `div.KwJkGe` split on "Preferred qualifications"

**JSON fields:** `company`, `fetched_date`, `title`, `organization`, `locations`, `experience_level`, `apply_url`, `about_the_job`, `minimum_qualifications`, `preferred_qualifications`, `responsibilities`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| Page is blank with requests/BS4 | JS-rendered. Use Selenium. `headless=False` works best to avoid bot detection. |
| `browser.back()` causes duplicates | Never navigate back from detail page. Always call `browser.get(url)` directly. |
| Icon text pollutes field values | Maintain `ICON_WORDS` set `{corporate_fare, place, bar_chart, …}` and strip from all text. |
| Re-runs scrape all pages again | 2 consecutive fully-skipped pages = stop. |

---

### Amazon Jobs

**Platform:** Public JSON API — no browser required

| | |
|---|---|
| **URL** | `amazon.jobs/en/search?job_type[]=Full-Time&category_type=Corporate&loc_query=India&country=IND` |
| **API endpoint** | `amazon.jobs/en/search.json` |
| **Pagination** | `offset=N`, increment by 10. `result_limit=10` is hard server cap. |
| **Auth** | None — fully public |
| **Concurrency** | Listing sequential (full JD in listing response — no separate detail call) |

**Strategy:** Pure `requests.Session()`. The `search.json` endpoint returns all fields in one call including description, qualifications, and responsibilities as HTML strings.

**JSON fields:** `company`, `fetched_date`, `title`, `organization`, `team`, `locations`, `experience_level`, `job_id`, `posted_date`, `apply_url`, `about_the_job`, `minimum_qualifications`, `preferred_qualifications`, `responsibilities`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| `result_limit` can't be increased | 10 is the hard server-side cap. Sending higher values is ignored. |
| HTML in description fields | Use BeautifulSoup to strip tags. Prefix `li` content with `•`. |

---

### JPMorgan Chase

**Platform:** Oracle HCM Recruiting Cloud — candidate-facing REST API

| | |
|---|---|
| **URL** | `jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs` |
| **API** | `recruitingCEJobRequisitions` (listing), `recruitingCEJobRequisitionDetails` (detail) |
| **Pagination** | `offset` inside the finder string — NOT as a separate query param |
| **Auth** | None — candidate-facing endpoints are fully public |
| **Page size** | 25 per page |
| **Concurrency** | 8 concurrent detail workers (ThreadPoolExecutor) |

**CRITICAL response structure:**
```
items[0].requisitionList[]   ← actual jobs (25 per page)
items[0].TotalJobsCount      ← use this for pagination stop
outer hasMore / count        ← always false/1 — IGNORE
```

**JSON fields:** `company`, `fetched_date`, `title`, `job_family`, `job_function`, `locations`, `job_id`, `posted_date`, `apply_url`, `summary`, `description`, `responsibilities`, `required_qualifications`, `preferred_qualifications`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| 400 Bad Request on finder param | Never pass via `params={}`. Build full URL as plain string. |
| Only 25 jobs returned, infinite loop | Unwrap `items[0].requisitionList` for jobs, `items[0].TotalJobsCount` for total. |
| offset param ignored | Offset inside finder string as `,offset=N` — not `&offset=`. |
| Id in detail finder needs quotes | `ById;Id=%2210690221%22,siteNumber=CX_1001` — Id wrapped in `%22`. |

---

### Goldman Sachs

**Platform:** `higher.gs.com` — Next.js SSG frontend over a GraphQL API

| | |
|---|---|
| **Strategy** | Pure requests — NO Selenium needed |
| **Listing** | POST to `/graphql` with `GetRoles` query. Pagination via `pageNumber` (0-indexed). |
| **Detail** | `GET https://higher.gs.com/_next/data/{buildId}/roles/{sourceId}.json` |
| **buildId** | Hash in homepage HTML — fetch once per run via `__NEXT_DATA__` script tag |
| **Dedup key** | `externalSource.externalApplicationUrl` (Oracle Cloud apply link) |
| **Concurrency** | 8 concurrent detail workers (ThreadPoolExecutor) |

**JSON fields:** `company`, `fetched_date`, `title`, `corporate_title`, `job_function`, `division`, `locations`, `source_id`, `apply_url`, `apply_url_browse`, `description`, `our_impact`, `your_impact`, `responsibilities`, `basic_qualifications`, `preferred_qualifications`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| buildId changes on deploys | Fetch homepage once per run and extract fresh buildId. |
| Apply URL vs browse URL | Use Oracle Cloud URL as canonical dedup key. Store `higher.gs.com` URL as `apply_url_browse`. |

---

### Mastercard

**Platform:** Phenom People (listing) + Workday (apply links only)

| | |
|---|---|
| **Listing endpoint** | `POST careers.mastercard.com/widgets?refNum=MASRUS&...&from=N&s=1` |
| **Auth** | Requires `PHPPPE_ACT` + `PLAY_SESSION` cookies — seed with GET first |
| **Page size** | 5 (server hard-cap) |
| **Dedup key** | `applyUrl` — Workday canonical URL |
| **Concurrency** | Sequential (Selenium detail) |

**Key pitfalls:**

| Problem | Fix |
|---|---|
| `/widgets` returns config, not jobs | `refNum`, `locale`, `siteType`, `pageId`, `channel` must be in URL AND POST body. |
| `from` offset ignored | Must appear as URL query param (`?from=N&s=1`), not just POST body. |
| Pagination broken | Seed `requests.Session` with GET to `/us/en/search-results` first for cookies. |
| `div.jd-info` not found | JS-rendered detail. Use Selenium headless, wait for `div.jd-info`. |

**JSON fields:** `company`, `fetched_date`, `title`, `req_id`, `category`, `locations`, `job_type`, `posted_date`, `apply_url`, `description_teaser`, `overview`, `role`, `all_about_you`, `qualifications`

---

### Visa

**Platform:** SmartRecruiters — fully public REST API

| | |
|---|---|
| **Listing API** | `GET api.smartrecruiters.com/v1/companies/visa/postings?country=in&limit=100&offset=N` |
| **Detail API** | `GET api.smartrecruiters.com/v1/companies/visa/postings/{postingId}` |
| **Auth** | None — fully public |
| **Dedup key** | `https://www.visa.co.in/en_in/jobs/{refNumber}` |
| **Concurrency** | 8 concurrent detail workers (ThreadPoolExecutor) |

**CRITICAL:** `city=` filter is exact-match. Pull all `country=in`, filter in-memory on `["bangalore","bengaluru","mumbai","bombay"]`.

**CRITICAL:** `jobAd.sections` is a **dict**, not a list. Access: `sections.get("jobDescription",{}).get("text","")`.

**JSON fields:** `company`, `fetched_date`, `title`, `ref_number`, `department`, `job_function`, `experience_level`, `type_of_employment`, `locations`, `posted_date`, `apply_url`, `job_description`, `qualifications`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| Only ~29 jobs instead of ~130 | `city=` exact-match misses "Bengaluru". Pull `country=in`, filter in-memory. |
| `'str' object has no attribute 'get'` | `sections` is a dict — never iterate, access by key. |

---

### Microsoft

**Platform:** Eightfold.ai ATS — fully public JSON API

| | |
|---|---|
| **Listing API** | `GET apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&start=N` |
| **Detail API** | `GET apply.careers.microsoft.com/api/pcsx/position_details?position_id=<id>&domain=microsoft.com` |
| **Auth** | None — fully public |
| **Dedup key** | `https://apply.careers.microsoft.com/careers/job/{id}` |
| **Concurrency** | 8 concurrent detail workers (ThreadPoolExecutor) |

**CRITICAL:** Only `/api/pcsx/position_details` has the JD. `/overrides` returns 200 OK but empty content — silent failure.

**CRITICAL:** Drop `pid=` from listing URL — it limits results to one profession.

**JSON fields:** `company`, `fetched_date`, `title`, `display_job_id`, `department`, `locations`, `work_location_option`, `posted_date`, `apply_url`, `overview`, `responsibilities`, `minimum_qualifications`, `preferred_qualifications`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| JD fields empty after 200 OK | Using `/overrides`. Always use `/position_details`. |
| Only one profession returned | Drop `pid=` from listing URL entirely. |
| `postedTs` is not a date | Unix timestamp. `datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")`. |

---

### Citi

**Platform:** Phenom People (SSR variant) — fully JS-rendered, no XHR API underneath

| | |
|---|---|
| **Site URL** | `jobs.citi.com/search-jobs/India/287/2/1269750/22/79/50/2` |
| **Strategy** | Selenium 2-pass |
| **Pagination** | Click `a.next` — URL never changes |
| **Total** | ~761 jobs / 15 per page / 51 pages |
| **Dedup key** | Full job URL: `https://jobs.citi.com/job/<city>/<slug>/287/<job_id>` |
| **Concurrency** | Sequential (Selenium, 2-pass required) |

**2-pass required:** Visiting detail pages mid-pagination resets browser to page 1. Pass 1 collects all stubs (browser stays on listing). Pass 2 scrapes details.

**Detail container:** `div.ats-description`. Wait for this, not the heading.

**CRITICAL:** Section headings vary per job — no fixed schema. Capture all `h2.jd-evergreen-hl` dynamically into single `job_description` field. Skip `CITI_SKIP_HEADINGS = {"discover your future at citi", "shape your career with citi"}`.

**JSON fields:** `company`, `fetched_date`, `title`, `locations`, `work_type`, `job_id`, `apply_url`, `job_description`

**Key pitfalls:**

| Problem | Fix |
|---|---|
| URL params ignored | `&p=N` silently ignored. Must click `a.next`. |
| `a.next` stays on last page | Compare first card URL before/after click. If unchanged, pagination is done. |
| Detail scraping breaks pagination | 2-pass only. Never interleave. |
| JD fields empty | Use `div.ats-description`, not `div.jd-info`. |
| Headings vary per job | Store as single `job_description` string. |

---

## Viewer Architecture

### Tracker Sidecar

`jobradar_tracker.json` keyed by `apply_url` (same as `jobs_db.json`):
```json
{
  "saved":   { "<apply_url>": true },
  "applied": { "<apply_url>": "2026-03-17" }
}
```

Survives DB refreshes. Keep permanently. Download via "Save Tracker" button (turns amber when dirty).

### Key Features
- 2-column card grid (toggle to 3 via `⊞`)
- Applied filter: Hide Applied (default) → All → Applied Only
- Company multi-select with checkboxes
- Score slider + distribution chart
- Right-side drawer for full JD (Escape closes)
- Unsaved changes warning on tab close

---

## Appendix — Adding a New Company Checklist

1. Open DevTools → Network tab → filter XHR/Fetch
2. Load careers page: JSON API? → requests. Zero XHR? → Selenium.
3. Identify platform (see Platform Identification Guide).
4. Check pagination signal and last-page condition.
5. Confirm auth — login wall or public?
6. Design company-specific JSON schema.
7. Concurrency model: API → 2-pass + ThreadPoolExecutor. Selenium → sequential or 2-pass.
8. Create `scrapers/<company>.py`, subclass `BaseScraper`, implement `scrape()`.
9. Register in `scrapers/__init__.py`.
10. Add to `COMPANIES_TO_RUN` and `API_SCRAPERS` or `SELENIUM_SCRAPERS` in `__main__.py`.

*Last updated: March 2026*
*Companies: Google, Amazon, JPMorgan Chase, Goldman Sachs, Mastercard, Visa, Microsoft, Citi*
*Architecture: plugin system (BaseScraper), concurrent detail fetching (ThreadPoolExecutor), thread-safe DB*
*Scorer: gpt-4o-mini, 10 concurrent workers, no batching, per-worker 429 backoff*
