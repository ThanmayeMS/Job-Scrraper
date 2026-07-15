"""
extract_work_profiles.py — Step 1 of the work-profile matching pipeline.

What it does:
  For every job in jobs_db.json that doesn't yet have an entry in work_profiles.json:
    1. Dumps all text fields from the job (no dependency on field_roles.json).
    2. Sends to GPT-4o-mini with an activity-extraction prompt.
    3. Gets back 3-5 sentences describing what the person actually does —
       stripped of tools, titles, qualifications, seniority, boilerplate.
    4. Writes {apply_url: work_profile_text} into work_profiles.json.

Why a separate file:
  - jobs_db.json stays untouched and doesn't grow.
  - work_profiles.json is lightweight (~1MB for 6K jobs) and easy to inspect/regenerate.
  - Incremental by design — safe to stop/resume anytime.

Usage:
    python extract_work_profiles.py                   # process all missing jobs
    python extract_work_profiles.py --status          # show coverage, no API calls
    python extract_work_profiles.py --dry-run         # test first 5 jobs, print output
    python extract_work_profiles.py --company google  # one company only
    python extract_work_profiles.py --workers 30      # more concurrency (Tier 2+)
    python extract_work_profiles.py --rerun           # redo all (overwrite existing)

Cost estimate : ~$0.0003/job → ~$1.75 for 5,826 jobs
Time estimate : ~8-12 min at 20 workers
Ctrl+C safe  : every completed job is flushed to disk immediately
"""

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

# ── PATHS — match your existing layout ───────────────────────────────────────

DB_FILE            = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job scraper\jobs_db.json"
WORK_DIR           = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job_scraper-v2"
WORK_PROFILES_FILE = WORK_DIR + r"\work_profiles.json"

OPENAI_API_KEY     = ""         # paste key here OR set OPENAI_API_KEY env var
MODEL              = "gpt-4o-mini"
DEFAULT_WORKERS    = 20
MAX_INPUT_CHARS    = 4000       # char cap on assembled job text sent to LLM

# ── FIELDS TO ALWAYS SKIP ────────────────────────────────────────────────────
#
# Metadata, IDs, URLs, short label fields — never useful as LLM input.
# We dump EVERYTHING else. No dependency on field_roles.json.
# The LLM handles mixed blobs (e.g. Citi job_description containing both
# responsibilities and qualifications) and extracts only activity language.

SKIP_FIELDS = {
    # metadata
    "apply_url", "apply_url_browse", "fetched_date", "posted_date",
    "job_id", "req_id", "ref_number", "source_id", "display_job_id",
    "company", "work_type", "type_of_employment", "work_location_option",
    "category", "job_type",
    # location — not activity signal
    "locations",
    # short label fields — 2-4 words, not useful input text
    "title", "organization", "team", "department", "division",
    "job_family", "job_function", "experience_level", "corporate_title",
}

# ── VALIDATION REFERENCE PROFILE ─────────────────────────────────────────────
#
# Used ONLY for ValidationScore — a scrape-time pre-score for offline analysis.
# This is NOT used in any product logic. Never filter or rank based on this.
# Purpose: correlate ValidationScore vs full LLM score to measure extraction quality.
# Replace with any profile to validate against a different reference.

VALIDATION_PROFILE = """
Current Role: Lead Advanced Analytics
Experience: 5+ years in data analytics, business intelligence, automation

What I do:
- Build analytics frameworks and KPI dashboards for senior leadership decisions
- Design automation platforms that eliminate manual reporting workflows
- Run customer segmentation, forecasting, and predictive modelling
- Build ETL pipelines and data infrastructure for operational analytics
- Partner cross-functionally with ops, finance, and product teams
- Deliver decision support tools that drive measurable business outcomes

Domain: Business Analytics, Business Intelligence, Advanced Analytics
Seniority: Senior / Lead individual contributor
"""

# ── PROMPT ────────────────────────────────────────────────────────────────────
#
# Five-field structured format:
#
#   Seniority       → seniority gap filter at query time
#   Domain          → hard domain pre-filter (zero LLM cost)
#   Output          → highest-signal embedding field (artefacts produced)
#   Work            → activity prose embedding
#   ValidationScore → scrape-time pre-score against VALIDATION_PROFILE
#                     FOR OFFLINE ANALYSIS ONLY — never used in product logic
#
# CV-side prompt in build_work_embeddings.py uses identical format minus
# ValidationScore (CV side has no JD to score against).

WORK_PROFILE_PROMPT = """\
Given the job description and reference profile below, write a structured \
summary in EXACTLY this format:

Seniority: <one of: Entry | Mid | Senior | Lead | Manager | Director | Executive>
Domain: <one of the domains listed below>
Output: <comma-separated list of 2-4 concrete artefacts this person produces>
Work: <3-5 sentences describing what this person does day-to-day>
ValidationScore: <integer 1-10 scoring fit against the reference profile>

─── SENIORITY RULES ───
Entry     = 0-2 years, analyst, associate, junior, graduate, intern
Mid       = 2-5 years, individual contributor, no management
Senior    = 5-8 years, senior IC, technical depth, may mentor others
Lead      = 8+ years OR leads a team of ICs without direct management title
Manager   = explicitly manages people, team lead with direct reports
Director  = director, VP, head of, principal with org-wide scope
Executive = C-suite, SVP, EVP, MD, Managing Director

─── DOMAIN OPTIONS (pick exactly one) ───
Business Analytics & BI       = analytics for business decisions, dashboards, KPIs, reporting
Data Engineering               = pipelines, ETL, data infrastructure, warehouses, lakes
Data Science & ML              = models, ML systems, experimentation, statistical research
Quantitative Research          = quant finance, risk models, pricing, mathematical modelling
Software Engineering           = building software products, applications, APIs, systems
DevOps & Infrastructure        = cloud infrastructure, CI/CD, platform engineering, SRE
Product Management             = product strategy, roadmap, user requirements, go-to-market
Operations Management          = running operations, vendor management, process management
Financial Analysis             = FP&A, accounting, treasury, financial reporting
Risk & Compliance              = risk frameworks, regulatory compliance, audit, controls
Consulting & Strategy          = strategy, advisory, client-facing problem solving
Other                          = use only if none of the above fit clearly

─── OUTPUT RULES ───
List the concrete deliverables this person produces — not what they consume.
Examples of GOOD outputs: "dashboards, forecasting models, ETL pipelines, automation scripts"
Examples of BAD outputs:  "insights, decisions, improvements, performance" (too vague)
Bad outputs signal a consumer role — someone who uses analytics, not builds it.

─── WORK RULES ───
- Start immediately with what the person does. No preamble.
- No "In this role" or "The individual will" or "This position involves".
- Write like: "Builds X for Y. Analyses Z to support W decisions. Designs..."
- Be specific to THIS role. No generic filler sentences.
- No tool or technology names.
- No company, division, or team names.
- No qualifications, education, or years of experience.
- No achievements, awards, or metrics.

─── VALIDATIONSCORE RULES ───
Score 1-10 how well this role matches the reference profile.
9-10 = Near-perfect match — same domain, same work type, same seniority level
7-8  = Strong match — same domain, mostly same work type, minor level difference
5-6  = Partial match — related domain, some overlap in work type
3-4  = Weak match — adjacent domain, limited overlap
1-2  = Poor match — different domain entirely or wrong seniority level

─── EXAMPLE OUTPUT ───
Seniority: Senior
Domain: Business Analytics & BI
Output: KPI dashboards, forecasting models, automated reports, segmentation frameworks
Work: Builds dashboards and reporting frameworks for senior leadership and operations teams. Analyses customer behaviour data to identify trends that inform business strategy. Designs forecasting models for quarterly planning cycles. Partners with finance and product teams to translate analytical findings into process improvements.
ValidationScore: 8

─── REFERENCE PROFILE ───
{validation_profile}

─── JOB DESCRIPTION ───
{job_text}

Return ONLY the five lines shown above. No extra text, no markdown, no explanation.
"""

# ── FIELD ASSEMBLY ────────────────────────────────────────────────────────────

def assemble_job_text(job: dict) -> str:
    """
    Dump all non-metadata text fields from the job into a single string.
    No field_roles.json needed — LLM handles messy/mixed field content.
    Capped at MAX_INPUT_CHARS to stay within token budget.
    """
    parts = []
    for field, val in job.items():
        if field in SKIP_FIELDS:
            continue
        if not val:
            continue
        if isinstance(val, list):
            val = "\n".join(str(v) for v in val if v)
        val = str(val).strip()
        if val:
            parts.append(val)

    return "\n\n".join(parts)[:MAX_INPUT_CHARS]


# ── DB / FILE I/O — thread-safe ───────────────────────────────────────────────

_lock = threading.Lock()


def load_db() -> dict:
    if not os.path.exists(DB_FILE):
        print(f"[!] jobs_db.json not found:\n    {DB_FILE}")
        sys.exit(1)
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {job.get("apply_url", f"job_{i}"): job for i, job in enumerate(data)}
    return data


def load_work_profiles() -> dict:
    if not os.path.exists(WORK_PROFILES_FILE):
        return {}
    with open(WORK_PROFILES_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def flush(profiles: dict):
    """Write work_profiles.json to disk. Always called inside _lock."""
    with open(WORK_PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)


def save_profile(profiles: dict, url: str, text: str):
    """Thread-safe: add entry and flush immediately so Ctrl+C loses nothing."""
    with _lock:
        profiles[url] = text
        flush(profiles)


# ── OUTPUT VALIDATION ────────────────────────────────────────────────────────

REQUIRED_FIELDS = ("Seniority:", "Domain:", "Output:", "Work:", "ValidationScore:")

def is_valid_output(text: str) -> bool:
    """Check all five required fields are present in the output."""
    return all(field in text for field in REQUIRED_FIELDS)

def parse_field(text: str, field: str) -> str:
    """Extract a single field value from the structured output."""
    for line in text.split("\n"):
        if line.strip().startswith(field):
            return line.split(":", 1)[1].strip()
    return ""


# ── SINGLE JOB EXTRACTOR ─────────────────────────────────────────────────────

def extract_one(client: OpenAI, job: dict) -> str:
    """
    Extract work profile for a single job.
    Returns the five-field structured string, or empty string if no text.
    Retries on rate limit (429) up to 3 times with exponential backoff.
    Retries once on malformed output (missing required fields).
    """
    job_text = assemble_job_text(job)

    if not job_text.strip():
        return ""   # recorded as empty — URL still saved so we don't retry it

    prompt = WORK_PROFILE_PROMPT.format(
        job_text=job_text,
        validation_profile=VALIDATION_PROFILE.strip(),
    )

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=350,     # five fields — slightly more tokens
            )
            result = response.choices[0].message.content.strip()

            # Validate — if fields missing, retry once with explicit reminder
            if not is_valid_output(result) and attempt < 2:
                reminder = (
                    f"{prompt}\n\n"
                    f"IMPORTANT: Your previous response was missing required fields. "
                    f"You MUST return exactly five lines starting with: "
                    f"Seniority:, Domain:, Output:, Work:, ValidationScore:"
                )
                response2 = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": reminder}],
                    temperature=0,
                    max_tokens=350,
                )
                result = response2.choices[0].message.content.strip()

            return result

        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                backoff = 15 * (attempt + 1)
                print(f"\n  [rate limit] backing off {backoff}s...", flush=True)
                time.sleep(backoff)
            elif attempt == 2:
                raise
            else:
                time.sleep(3)

    return ""


# ── PROGRESS ──────────────────────────────────────────────────────────────────

class Progress:
    def __init__(self, total: int):
        self.total  = total
        self.done   = 0
        self.ok     = 0
        self.errors = 0
        self.empty  = 0
        self.start  = time.time()
        self._lock  = threading.Lock()

    def record(self, success: bool, empty: bool, company: str):
        with self._lock:
            self.done += 1
            if empty:
                self.empty += 1
            elif success:
                self.ok += 1
            else:
                self.errors += 1
            self._render(company, success)

    def _render(self, company: str, success: bool):
        elapsed  = time.time() - self.start
        avg      = elapsed / self.done if self.done else 1
        eta_sec  = (self.total - self.done) * avg
        eta_str  = f"{int(eta_sec//60)}m{int(eta_sec%60)}s"
        pct      = int(100 * self.done / self.total)
        filled   = int(30 * self.done / self.total)
        bar      = "█" * filled + "░" * (30 - filled)
        co_short = (company[:14] + "…") if len(company) > 15 else company
        status   = "✓" if success else "✗"
        print(
            f"\r[{bar}] {pct:3d}%  {self.done}/{self.total}  "
            f"ETA {eta_str}  ✓{self.ok} ✗{self.errors} ø{self.empty}  "
            f"{status} {co_short:<15}",
            end="", flush=True
        )


# ── CONCURRENT RUNNER ─────────────────────────────────────────────────────────

def run(client: OpenAI, to_process: list, profiles: dict, workers: int) -> "Progress":
    progress = Progress(total=len(to_process))
    stop     = threading.Event()

    def task(job: dict):
        if stop.is_set():
            return
        url     = job.get("apply_url", "")
        company = job.get("company", "?")
        try:
            result = extract_one(client, job)
            save_profile(profiles, url, result)
            progress.record(success=True, empty=(result == ""), company=company)
        except Exception:
            progress.record(success=False, empty=False, company=company)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task, job): job for job in to_process}
            for future in as_completed(futures):
                if stop.is_set():
                    break
                try:
                    future.result()
                except Exception:
                    pass
    except KeyboardInterrupt:
        stop.set()
        print(f"\n\n⛔  Stopped. {progress.ok} profiles saved this session.")
        print(f"   Resume anytime: python extract_work_profiles.py")
        raise

    return progress


# ── STATUS DISPLAY ────────────────────────────────────────────────────────────

def print_status(db: dict, profiles: dict, workers: int):
    jobs      = list(db.values())
    total     = len(jobs)
    done      = sum(1 for j in jobs if j.get("apply_url", "") in profiles)
    remaining = total - done
    eta_sec   = (remaining / workers) * 3 if workers else remaining * 3

    by_co: dict = {}
    for j in jobs:
        c   = j.get("company", "Unknown")
        url = j.get("apply_url", "")
        by_co.setdefault(c, [0, 0])
        by_co[c][0] += 1
        if url in profiles:
            by_co[c][1] += 1

    def bar(n, t, w=15):
        f = int(w * n / t) if t else 0
        return f"[{'█'*f}{'░'*(w-f)}] {n}/{t}"

    print(f"\n{'═'*55}")
    print(f"  WORK PROFILE EXTRACTION STATUS")
    print(f"{'═'*55}")
    print(f"  Total jobs : {total}")
    print(f"  Extracted  : {bar(done, total, 20)}")
    print(f"  Remaining  : {remaining}  "
          f"ETA ~{int(eta_sec//60)}m{int(eta_sec%60)}s at {workers} workers")
    print(f"\n  By company:")
    for c, (tot, sc) in sorted(by_co.items()):
        print(f"    {c:<22}  {bar(sc, tot)}")
    print(f"\n  Output: {WORK_PROFILES_FILE}")
    print(f"{'═'*55}\n")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract activity-focused work profiles from jobs_db.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract_work_profiles.py                   # process all missing jobs
  python extract_work_profiles.py --status          # coverage stats, no API calls
  python extract_work_profiles.py --dry-run         # test first 5 jobs, print output
  python extract_work_profiles.py --company google  # one company only
  python extract_work_profiles.py --workers 30      # more concurrency (Tier 2+)
  python extract_work_profiles.py --rerun           # redo everything from scratch
        """
    )
    parser.add_argument("--status",  action="store_true",
                        help="Show extraction coverage, no API calls.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test on first 5 jobs — print profiles, do not save.")
    parser.add_argument("--company", type=str, default=None,
                        help="Process one company only (e.g. 'google', 'visa').")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Concurrent API workers (default {DEFAULT_WORKERS}).")
    parser.add_argument("--rerun",   action="store_true",
                        help="Re-extract all jobs, overwriting existing work_profiles.json.")
    args = parser.parse_args()

    db       = load_db()
    profiles = {} if args.rerun else load_work_profiles()

    if args.status:
        print_status(db, profiles, args.workers)
        return

    # Build candidate list
    jobs = list(db.values())

    if args.company:
        target = args.company.strip().lower()
        jobs   = [j for j in jobs if j.get("company", "").strip().lower() == target]
        if not jobs:
            available = sorted(set(j.get("company", "").lower() for j in db.values()))
            print(f"[!] No jobs found for company '{args.company}'.")
            print(f"    Available: {available}")
            sys.exit(1)

    to_process = [j for j in jobs
                  if args.rerun or j.get("apply_url", "") not in profiles]

    if args.dry_run:
        to_process = to_process[:5]

    print_status(db, profiles, args.workers)

    if not to_process and not args.dry_run:
        print("✅  All jobs already extracted. Use --rerun to redo.\n")
        return

    # Resolve API key
    api_key = OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("❌  No API key found. Set OPENAI_API_KEY env var or paste in script.")
        sys.exit(1)
    client = OpenAI(api_key=api_key)

    # Dry run — print and exit without saving
    if args.dry_run:
        print(f"  DRY RUN — {len(to_process)} jobs\n{'─'*55}\n")
        for job in to_process:
            text = assemble_job_text(job)
            print(f"  [{job.get('company','?').upper()}]  {job.get('title','?')}")
            print(f"  Input chars : {len(text)}")
            print(f"  Input preview:\n  {text[:400]}\n  ...\n")
            profile = extract_one(client, job)
            print(f"  ── EXTRACTED WORK PROFILE ──")
            print(f"  {profile}")
            print(f"  {'─'*55}\n")
        return

    # Full run
    eta_sec = (len(to_process) / args.workers) * 3
    print(f"  Processing {len(to_process)} jobs  |  "
          f"{args.workers} workers  |  "
          f"ETA ~{int(eta_sec//60)}m{int(eta_sec%60)}s")
    print(f"  Ctrl+C anytime — every job saved immediately\n")

    try:
        progress = run(client, to_process, profiles, args.workers)
        elapsed  = time.time() - progress.start
        print(f"\n\n{'─'*55}")
        print(f"  ✅  Extracted : {progress.ok}")
        print(f"  ø   Empty    : {progress.empty}  (job had no text fields)")
        print(f"  ❌  Errors   : {progress.errors}")
        print(f"  ⏱   Time     : {int(elapsed//60)}m{int(elapsed%60)}s")
        if elapsed > 0 and progress.ok > 0:
            print(f"  ⚡  Rate     : {progress.ok / elapsed * 60:.0f} jobs/min")
        print(f"{'─'*55}\n")
    except KeyboardInterrupt:
        pass

    print_status(db, profiles, args.workers)


if __name__ == "__main__":
    main()