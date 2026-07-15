"""
score_jobs_openai.py — Job similarity scorer using OpenAI (async, concurrent)
Model: gpt-4o-mini

Strategy: 10 concurrent single-job calls, no sleep, no batching.
Each job gets its own focused prompt → full accuracy preserved.
10 workers × ~4s latency = ~760 jobs in ~5 minutes vs ~50 minutes sequential.

Usage:
    python -m job_scraper_pkg --parallel          # score all unscored jobs
    python score_jobs_openai.py --batch 300       # score next 300 unscored jobs
    python score_jobs_openai.py --status          # show progress, no scoring
    python score_jobs_openai.py --dry-run         # test on first 5 jobs only
    python score_jobs_openai.py --top 20 --min 7  # print top matches
    python score_jobs_openai.py --rescore         # re-score everything
    python score_jobs_openai.py --workers 15      # override concurrency (default 10)

Ctrl+C at any time — progress is saved after every completed job, safe to stop/resume.
"""

import json
import argparse
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ── CONFIG ────────────────────────────────────────────────────────────────────

DB_PATH        = "jobs_db.json"
MODEL          = "gpt-4o-mini"
DEFAULT_WORKERS = 10   # concurrent API calls — safe for gpt-4o-mini Tier 1 (500 RPM)
                        # increase to 20-30 if you're on Tier 2+

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


# ── YOUR RESUME ───────────────────────────────────────────────────────────────

RESUME_TEXT = """
Name: Koushik Majeti
Current Role: Lead Advanced Analytics, American Express
Experience: 5+ years

TECHNICAL SKILLS:
SQL (Hive, GCP), Python, MS Excel (Advanced + Automation), Tableau, Power BI,
Statistical Testing, EDA, ETL, KPI Dashboard Development, Automation Scripting,
Generative AI (Prompt Engineering), HTML/CSS/JavaScript (D3.js, Chart.js),
Custom Visualization Building

CORE COMPETENCIES:
Data Analytics & Visualization, Business Intelligence, Automation & Workflow Optimization,
KPI Development & Performance Tracking, Generative AI in Analytics,
Process Improvement & Operational Efficiency, Stakeholder Management,
Cross-functional Collaboration, Data Storytelling

WORK EXPERIENCE:

Lead Advanced Analytics @ American Express, Gurugram (Jun 2024 – Present)
- Built SPLIT: a JavaScript + GenAI tool for multivariate metric decomposition
- Tableau/Power BI dashboards influencing senior leadership decisions
- Cross-functional analytics alignment; mentoring junior analysts
- 2x American Express Tribute Award winner

Senior Data Analyst @ American Express (Mar 2022 – Jun 2024)
- Analytics frameworks for customer care metrics → $2M+ annualized savings
- Built PYMagix: automation platform used by 200+ users, saves 20-25 hrs/user/month
- End-to-end ETL infrastructure for servicing profiles
- Customer behavior analysis for digital growth

Business Systems Analyst @ American Express (Aug 2020 – Mar 2022)
- Monthly forecasting with trend/seasonal decomposition
- Clustering-based customer segmentation
- Predictive models embedded in workflow automation
- ETL pipelines for unstructured data

EDUCATION:
B.Tech, Electronics & Communication Engineering
IIIT Naya Raipur | GPA: 8.6/10 | 2020

CERTIFICATIONS:
- Harvard Leadership Edge (Executive Leadership, Influence, Decision-Making)
- American Express Platinum Leadership Program

LOCATION: Gurugram, Haryana, India
JOB TARGET: Data Science, Business Intelligence, Advanced Analytics, Data Engineering
PREFERRED: Mid to Senior level, Full-time, India locations
"""

# ── SCORING PROMPT ────────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """You are a recruiter evaluating job fit. Given a candidate resume and job description, return ONLY a valid JSON object. No markdown, no explanation, no extra text.

JSON schema (return exactly this structure):
{{
  "score": <integer 1-10>,
  "reason": "<2 sentences explaining the score>",
  "matching_skills": ["<skill1>", "<skill2>", "<skill3>"],
  "gaps": "<1 sentence on what candidate is missing for this role>",
  "info_level": "<Sufficient, Partial, or Insufficient>"
}}

Scoring guide:
9-10 = Near-perfect fit. Candidate meets almost all requirements.
7-8  = Strong fit. Qualifies for most requirements, minor gaps.
5-6  = Moderate fit. Relevant background but notable missing skills.
3-4  = Weak fit. Some transferable skills, significant gaps.
1-2  = Poor fit. Wrong domain or experience level entirely.

Info Level guide:
Sufficient   = Job description clearly outlines responsibilities and requirements.
Partial      = Brief description, gives a general idea of the role.
Insufficient = Extremely vague, boilerplate only, or missing core requirements.

---
CANDIDATE RESUME:
{resume}

---
JOB DESCRIPTION:
{job_text}
"""

# ── FIELD EXTRACTION ──────────────────────────────────────────────────────────

TEXT_FIELDS = [
    "title", "organization", "about_the_job", "responsibilities",
    "minimum_qualifications", "preferred_qualifications",
    "description", "summary", "job_description", "qualifications",
    "required_qualifications", "basic_qualifications",
    "our_impact", "your_impact", "overview", "role", "all_about_you",
    "job_function", "job_family", "experience_level", "department",
]

def extract_job_text(job: dict) -> str:
    parts = []
    for field in TEXT_FIELDS:
        val = job.get(field)
        if isinstance(val, str) and val.strip():
            parts.append(f"[{field.upper()}]\n{val.strip()}")
        elif isinstance(val, list):
            joined = " ".join(str(v) for v in val if v)
            if joined.strip():
                parts.append(f"[{field.upper()}]\n{joined.strip()}")
    return "\n\n".join(parts)[:4000]

# ── DB — thread-safe ──────────────────────────────────────────────────────────

_db_lock = threading.Lock()

def load_db() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {job.get("apply_url", f"job_{i}"): job for i, job in enumerate(data)}
    return data

def save_db(db: dict):
    """Thread-safe write. Only one thread writes at a time."""
    with _db_lock:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(list(db.values()), f, indent=2, ensure_ascii=False)

def upsert_result(db: dict, url: str, result: dict):
    """Thread-safe: merge scoring result into db entry and flush to disk."""
    with _db_lock:
        if url in db:
            db[url]["similarity_score"] = result["score"]
            db[url]["match_reason"]     = result.get("reason", "")
            db[url]["matching_skills"]  = result.get("matching_skills", [])
            db[url]["match_gaps"]       = result.get("gaps", "")
            db[url]["info_level"]       = result.get("info_level", "Unknown")
        # Write immediately after every job so Ctrl+C loses nothing
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(list(db.values()), f, indent=2, ensure_ascii=False)

# ── SINGLE JOB SCORER ─────────────────────────────────────────────────────────

def score_one(client: OpenAI, job: dict) -> dict:
    """
    Score a single job. Runs in a worker thread.
    Returns the parsed result dict, or raises on failure.
    No sleep — let OpenAI's own rate limiting handle throttling.
    If rate-limited (429), back off and retry up to 3 times.
    """
    job_text = extract_job_text(job)
    if not job_text.strip():
        return {
            "score": 0,
            "reason": "No job description available.",
            "matching_skills": [],
            "gaps": "N/A",
            "info_level": "Insufficient"
        }

    prompt = PROMPT_TEMPLATE.format(
        resume=RESUME_TEXT.strip(),
        job_text=job_text
    )

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()
            return json.loads(raw)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                # Rate limited — back off and retry
                backoff = 10 * (attempt + 1)
                print(f"\n  [rate limit] backing off {backoff}s...", flush=True)
                time.sleep(backoff)
            elif attempt == 2:
                raise
            else:
                time.sleep(2)

# ── PROGRESS DISPLAY ──────────────────────────────────────────────────────────

class Progress:
    """Thread-safe progress counter with live console output."""

    def __init__(self, total: int):
        self.total   = total
        self.done    = 0
        self.scored  = 0
        self.errors  = 0
        self.start   = time.time()
        self._lock   = threading.Lock()

    def record(self, success: bool, company: str, title: str, score: int = 0):
        with self._lock:
            self.done += 1
            if success:
                self.scored += 1
            else:
                self.errors += 1
            self._render(company, title, score, success)

    def _render(self, company: str, title: str, score: int, success: bool):
        elapsed   = time.time() - self.start
        avg       = elapsed / self.done if self.done else 1
        eta_sec   = (self.total - self.done) * avg
        eta_str   = f"{int(eta_sec//60)}m{int(eta_sec%60)}s"
        pct       = int(100 * self.done / self.total)
        filled    = int(30 * self.done / self.total)
        bar       = "█" * filled + "░" * (30 - filled)
        score_str = f"{score}/10" if success else "err"
        co_short  = (company[:10] + "…") if len(company) > 11 else company
        ti_short  = (title[:22] + "…")   if len(title)   > 23 else title
        print(
            f"\r[{bar}] {pct:3d}%  {self.done}/{self.total}  "
            f"ETA {eta_str}  ✓{self.scored} ✗{self.errors}  "
            f"{co_short:<11} {score_str:<6} {ti_short:<25}",
            end="", flush=True
        )

# ── CONCURRENT RUNNER ─────────────────────────────────────────────────────────

def run_concurrent(client: OpenAI, db: dict, to_score: list, workers: int):
    """
    Score all jobs in to_score using a thread pool.
    Each job is independent — no shared mutable state except db (lock-protected).
    Workers fire as fast as OpenAI allows; 429s trigger per-worker backoff.
    """
    progress = Progress(total=len(to_score))
    stop     = threading.Event()  # set on KeyboardInterrupt

    def score_task(job: dict):
        if stop.is_set():
            return
        url     = job.get("apply_url", "")
        company = job.get("company", "?")
        title   = job.get("title", "?")
        try:
            result = score_one(client, job)
            upsert_result(db, url, result)
            progress.record(True, company, title, result["score"])
        except Exception as e:
            progress.record(False, company, title)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(score_task, job): job for job in to_score}
            for future in as_completed(futures):
                if stop.is_set():
                    break
                # Exceptions are already caught inside score_task
                try:
                    future.result()
                except Exception:
                    pass
    except KeyboardInterrupt:
        stop.set()
        print(f"\n\n⛔  Stopped. Progress saved — {progress.scored} scored this session.")
        print("   Resume anytime: python score_jobs_openai.py")
        raise

    return progress

# ── REPORT ────────────────────────────────────────────────────────────────────

def print_status(db: dict, workers: int):
    jobs    = list(db.values())
    total   = len(jobs)
    if not total:
        print("\nDatabase is empty."); return
    scored  = [j for j in jobs if "similarity_score" in j]
    unscored = total - len(scored)
    scores  = [j["similarity_score"] for j in scored]
    # Estimated time: (unscored / workers) * 4s avg per call
    eta_sec = (unscored / workers) * 4 if workers else unscored * 4
    eta_str = f"~{int(eta_sec//60)}m{int(eta_sec%60)}s" if eta_sec > 0 else "done"

    def bar(n, t, w=20):
        f = int(w*n/t) if t else 0
        return f"[{'█'*f}{'░'*(w-f)}] {n}/{t}"

    print(f"\n{'═'*55}")
    print(f"  SCORING STATUS  ({workers} concurrent workers)")
    print(f"{'═'*55}")
    print(f"  Total      : {total}")
    print(f"  Scored     : {bar(len(scored), total)}")
    print(f"  Remaining  : {unscored}  ETA {eta_str}")
    if scores:
        print(f"\n  Breakdown  :")
        for rng, label in [
            (range(9,11), "🟢 9-10 Excellent"),
            (range(7,9),  "🟡 7-8  Strong   "),
            (range(5,7),  "🟠 5-6  Moderate "),
            (range(0,5),  "🔴 0-4  Weak     "),
        ]:
            n = sum(1 for s in scores if s in rng)
            print(f"    {label} : {n}")
    by_co: dict = {}
    for j in jobs:
        c = j.get("company", "Unknown")
        by_co.setdefault(c, [0, 0])
        by_co[c][0] += 1
        if "similarity_score" in j:
            by_co[c][1] += 1
    print(f"\n  By company :")
    for c, (tot, sc) in sorted(by_co.items()):
        f = int(15 * sc / tot) if tot else 0
        b = f"[{'█'*f}{'░'*(15-f)}] {sc}/{tot}"
        print(f"    {c:<22} {b}")
    print(f"{'═'*55}\n")


def print_top_jobs(db: dict, top_n: int, min_score: float):
    scored  = [j for j in db.values() if "similarity_score" in j]
    ranked  = sorted(
        [j for j in scored if j["similarity_score"] >= min_score],
        key=lambda x: x["similarity_score"], reverse=True
    )
    print(f"\n{'═'*70}")
    print(f"  TOP {top_n} MATCHES  (score ≥ {min_score})  —  {len(ranked)} qualifying")
    print(f"{'═'*70}")
    for i, job in enumerate(ranked[:top_n], 1):
        locs   = job.get("locations", "")
        if isinstance(locs, list): locs = ", ".join(str(l) for l in locs)
        skills = ", ".join(job.get("matching_skills", []))
        print(f"""
#{i}  [{job['similarity_score']}/10]  {job.get('company','?').upper()} — {job.get('title','?')}
    📍 {locs}
    💡 {job.get('match_reason', job.get('reason',''))}
    ✅ {skills}
    ⚠️  {job.get('match_gaps', job.get('gaps',''))}
    🔗 {job.get('apply_url','')}""")
    print(f"\n{'═'*70}\n")

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Score jobs_db.json using OpenAI (async)")
    parser.add_argument("--rescore",  action="store_true", help="Re-score already-scored jobs")
    parser.add_argument("--dry-run",  action="store_true", help="Test on first 5 jobs only")
    parser.add_argument("--status",   action="store_true", help="Show progress, no scoring")
    parser.add_argument("--batch",    type=int, default=0,  help="Score next N unscored jobs")
    parser.add_argument("--top",      type=int, default=0,  help="Print top N jobs after scoring")
    parser.add_argument("--min",      type=float, default=0, help="Min score for --top")
    parser.add_argument("--workers",  type=int, default=DEFAULT_WORKERS, help="Concurrent workers")
    args = parser.parse_args()

    db = load_db()

    if args.status:
        print_status(db, args.workers)
        if args.top > 0:
            print_top_jobs(db, args.top, args.min)
        return

    api_key = ""
    if not api_key:
        print("❌  OPENAI_API_KEY not set. Export it or paste it in the script.")
        sys.exit(1)
    client = OpenAI(api_key=api_key)

    jobs     = list(db.values())
    to_score = [j for j in jobs if args.rescore or "similarity_score" not in j]
    if args.dry_run:
        to_score = to_score[:5]
    elif args.batch > 0:
        to_score = to_score[:args.batch]

    print_status(db, args.workers)

    if not to_score:
        print("✅  Nothing to score. Use --rescore to redo all.")
        if args.top > 0:
            print_top_jobs(db, args.top, args.min)
        return

    eta_sec = (len(to_score) / args.workers) * 4
    print(f"  Scoring {len(to_score)} jobs  |  {args.workers} workers  |  ETA ~{int(eta_sec//60)}m{int(eta_sec%60)}s")
    print(f"  Ctrl+C anytime — every completed job is already saved\n")

    try:
        progress = run_concurrent(client, db, to_score, args.workers)
        elapsed  = time.time() - progress.start
        print(f"\n\n{'─'*55}")
        print(f"  ✅ Scored   : {progress.scored}")
        print(f"  ❌ Errors   : {progress.errors}")
        print(f"  ⏱  Time     : {int(elapsed//60)}m{int(elapsed%60)}s")
        print(f"  ⚡ Rate     : {progress.scored / elapsed * 60:.0f} jobs/min")
        print(f"{'─'*55}\n")
        print_status(db, args.workers)
    except KeyboardInterrupt:
        pass

    if args.top > 0:
        print_top_jobs(db, args.top, args.min)


if __name__ == "__main__":
    main()
