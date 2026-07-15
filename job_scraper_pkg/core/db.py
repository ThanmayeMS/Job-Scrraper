"""
core/db.py — Shared database helpers used by all scrapers.

Thread-safe: _db_lock ensures only one thread writes to disk at a time.
Multiple scrapers can call upsert_job() concurrently — the lock serialises
the in-memory update and disk flush so no data is lost or corrupted.

Ctrl+C safe: all writes go through _atomic_save() which writes to a .tmp
file first, keeps a .bak of the previous version, then does an atomic
os.replace(). A Ctrl+C mid-write can corrupt the .tmp file but never
touches the live jobs_db.json or its .bak. Worst case: you lose the jobs
from the current interrupted batch — never the entire DB.

Recovery: load_db() automatically falls back to jobs_db.json.bak if the
main file is missing or corrupt. No manual intervention needed.
"""

import json
import os
import shutil
import threading
from datetime import date

DB_FILE  = "jobs_db.json"
LOG_FILE = "jobs_daily_log.json"
TODAY    = date.today().strftime("%Y-%m-%d")

_db_lock = threading.Lock()


# ── Atomic save — the only write path for DB files ───────────────────────────
def _atomic_save(data: list | dict, filepath: str):
    """
    Write data to disk atomically.

    Strategy:
      1. Serialise to filepath.tmp  (new content)
      2. Copy current filepath      → filepath.bak  (previous version)
      3. os.replace(tmp → filepath) (atomic swap — cannot be interrupted)

    If a Ctrl+C fires during step 1, filepath is untouched.
    If it fires during step 3, the OS guarantees the swap either completed
    or did not happen — filepath is never left in a partial state.

    The .bak file means you always have the last known-good version on disk.
    """
    tmp    = filepath + ".tmp"
    backup = filepath + ".bak"

    # Step 1: write new content to temp file
    payload = data if isinstance(data, list) else list(data.values())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Step 2: snapshot current live file as backup (before we overwrite it)
    if os.path.exists(filepath):
        shutil.copy2(filepath, backup)

    # Step 3: atomic replace
    os.replace(tmp, filepath)


# ── DB load — falls back to .bak if main file is corrupt ─────────────────────
def load_db(filepath: str = DB_FILE) -> dict:
    """
    Load jobs_db.json as a dict keyed by apply_url.

    Tries filepath first. If missing or corrupt, automatically tries
    filepath.bak (the previous known-good version written by _atomic_save).
    """
    candidates = [filepath, filepath + ".bak"]

    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if path.endswith(".bak"):
                    print(f"[!] Main DB was corrupt or missing — loaded from backup: {path}")
                    print(f"    Re-saving clean copy to {filepath}…")
                    # Restore the backup as the live file immediately
                    shutil.copy2(path, filepath)
                if isinstance(data, list):
                    return {j["apply_url"]: j for j in data if j.get("apply_url")}
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                print(f"[!] Could not parse {path} — {'trying backup…' if path == filepath else 'giving up.'}")
                continue

    print("[!] No valid DB found — starting fresh.")
    return {}


# ── DB save — always atomic ───────────────────────────────────────────────────
def save_db(db: dict, filepath: str = DB_FILE):
    """
    Write db to disk atomically.
    Acquires _db_lock — safe to call from multiple threads.
    """
    with _db_lock:
        _atomic_save(list(db.values()), filepath)


# ── Upsert — insert new job, skip if exists ───────────────────────────────────
def upsert_job(db: dict, job: dict) -> bool:
    """
    Insert job if apply_url not already in db.
    Sets fetched_date once via setdefault — never overwritten on re-run.
    Acquires lock for both the dict update and disk flush.
    Returns True if inserted (new job), False if skipped (already exists).

    Uses _atomic_save for the per-job flush — Ctrl+C cannot corrupt the DB.
    Worst case on interruption: the current job batch is not saved, but all
    previously saved jobs remain intact (either in main file or .bak).
    """
    key = job.get("apply_url", "")
    if not key:
        return False
    with _db_lock:
        if key in db:
            return False
        job.setdefault("fetched_date", TODAY)
        db[key] = job
        # Per-job flush — crash-safe at the individual job level
        _atomic_save(list(db.values()), DB_FILE)
    return True


# ── Log helpers ───────────────────────────────────────────────────────────────
def load_log(filepath: str = LOG_FILE) -> list:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


RECENT_FILE = "jobs_recent.json"


def flush_recent(db: dict, days: int, filepath: str = RECENT_FILE):
    """
    Write a filtered snapshot of jobs_db to jobs_recent.json.
    Includes only jobs whose fetched_date is within the last N days.
    Runs after every scrape — no manual trigger needed.

    Uses fetched_date (always present, set on first insert) as the filter field.
    Sorted newest-first so the viewer loads in chronological order.

    Why a separate file and not a filter in the viewer:
      - Keeps jobs_db.json as the single source of truth (never modified)
      - jobs_recent.json is disposable — regenerated every run
      - Scorer and other tools can point at jobs_recent.json for faster iteration
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    recent = [
        job for job in db.values()
        if (job.get("fetched_date") or "") >= cutoff
    ]

    # Sort newest first
    recent.sort(key=lambda j: j.get("fetched_date", ""), reverse=True)

    # Atomic write — same safety as main DB
    _atomic_save(recent, filepath)

    print(f"\n  [recent] {len(recent)} jobs from last {days} days → {filepath}")


def append_log(company: str, new_jobs: int, filepath: str = LOG_FILE):
    """Append a daily log entry. Thread-safe. Atomic write."""
    with _db_lock:
        log = load_log(filepath)
        for entry in log:
            if entry.get("date") == TODAY and entry.get("company") == company:
                entry["new_jobs"] = entry.get("new_jobs", 0) + new_jobs
                break
        else:
            log.append({"date": TODAY, "company": company, "new_jobs": new_jobs})
        log.sort(key=lambda e: (e.get("date", ""), e.get("company", "")), reverse=True)
        # Atomic write — log file is also protected
        _atomic_save(log, filepath)
    print(f"  [log] {TODAY} | {company} | +{new_jobs} jobs")
