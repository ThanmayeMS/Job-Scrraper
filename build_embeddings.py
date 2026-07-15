"""
build_work_embeddings.py — Step 2 of the work-profile matching pipeline.

What it does:
  Phase 1 — CV work profile extraction (once, cached in cv_work_profile.txt)
    Sends your resume text through the same activity-extraction prompt used
    for jobs. Produces plain prose: "what this person has been doing day-to-day."
    Stored in cv_work_profile.txt so you can inspect and edit it.

  Phase 2 — Job work profile embedding (incremental, batched)
    Reads work_profiles.json, embeds any URL not yet in the index.
    Output: work_embeddings_large.npy  +  work_embed_index_large.json

  Phase 3 — CV work profile embedding
    Embeds the text in cv_work_profile.txt.
    Output: profile_work_embedding_large.npy

Why this matters:
  Both sides (job and CV) go through the same "activities only" extraction
  before embedding. This puts them in the same vector space — you are
  comparing work description to work description, not CV-soup to JD-soup.
  The cosine threshold can then be raised significantly (0.45 → 0.65+),
  cutting the LLM scoring pool from ~55% to ~20% of the DB.

Usage:
    python build_work_embeddings.py               # full run (all 3 phases)
    python build_work_embeddings.py --status      # show state, no API calls
    python build_work_embeddings.py --cv-only     # re-extract + re-embed CV only
    python build_work_embeddings.py --embed-only  # skip CV extraction, embed jobs only
    python build_work_embeddings.py --re-extract-cv  # force re-extract CV profile

Cost estimate : ~$0.00008/job embedding + ~$0.001 CV extraction = ~$0.47 for 5,826 jobs
Time estimate : ~3-5 min (embedding is fast — batched, not per-job API calls)
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from openai import OpenAI

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


# ── PATHS — match your existing layout exactly ────────────────────────────────

DB_FILE              = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job scraper\jobs_db.json"
WORK_DIR             = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job_scraper-v2"

WORK_PROFILES_FILE   = WORK_DIR + r"\work_profiles.json"
CV_WORK_PROFILE_FILE = WORK_DIR + r"\cv_work_profile.txt"   # inspectable plain text

# Work embedding outputs — parallel to existing embeddings_large.npy, never overwrite it
WORK_EMB_FILE        = WORK_DIR + r"\work_embeddings_large.npy"
WORK_IDX_FILE        = WORK_DIR + r"\work_embed_index_large.json"
PROFILE_WORK_EMB_FILE= WORK_DIR + r"\profile_work_embedding_large.npy"

OPENAI_API_KEY       = ""       # paste key here OR set OPENAI_API_KEY env var
EMBED_MODEL          = "text-embedding-3-large"
CHAT_MODEL           = "gpt-4o-mini"
EMBED_DIMS           = 3072
BATCH_SIZE           = 128
MAX_PROFILE_CHARS    = 4000     # cap on resume text sent to CV extraction prompt

# ── YOUR RESUME TEXT ──────────────────────────────────────────────────────────
# Same PROFILE_TEXT from build_embeddings.py — used as input to CV extraction.
# The LLM will distil this into activity prose (same format as job work profiles).

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

# ── PROMPTS ───────────────────────────────────────────────────────────────────
#
# CV-side prompt mirrors the job-side prompt in extract_work_profiles.py exactly.
# Same four fields, same domain list, same output rules.
# "Has been doing" (CV) vs "will be doing" (JD) — comparing like with like.
# Both sides must use identical format so they land in the same embedding space.

CV_EXTRACTION_PROMPT = """\
Given the CV below, write a structured summary in EXACTLY this format:

Seniority: <one of: Entry | Mid | Senior | Lead | Manager | Director | Executive>
Domain: <one of the domains listed below>
Output: <comma-separated list of 2-4 concrete artefacts this person produces>
Work: <3-5 sentences describing what this person has been doing day-to-day>

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

─── WORK RULES ───
- Start immediately with what the person does. No preamble.
- No "In this role" or "The individual has been" or "This person".
- Write like: "Builds X for Y. Analyses Z to support W decisions. Designs..."
- Be specific to what this person actually does. No generic filler.
- No tool or technology names.
- No company or employer names.
- No qualifications, education, or certifications.
- No achievements, awards, or metrics.

─── EXAMPLE OUTPUT ───
Seniority: Senior
Domain: Business Analytics & BI
Output: KPI dashboards, forecasting models, automated reports, segmentation frameworks
Work: Builds dashboards and reporting frameworks for senior leadership and operations teams. Analyses customer behaviour data to identify trends that inform business strategy. Designs forecasting models for quarterly planning cycles. Partners with finance and product teams to translate analytical findings into process improvements.

CV:
{resume_text}

Return ONLY the four lines shown above. No extra text, no markdown, no explanation.
"""

# ── I/O HELPERS ───────────────────────────────────────────────────────────────

def load_json(filepath: str, default):
    if not os.path.exists(filepath):
        return default
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default

def save_json(obj, filepath: str):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

def load_existing_embeddings(emb_file: str, idx_file: str, dims: int):
    if os.path.exists(emb_file) and os.path.exists(idx_file):
        matrix = np.load(emb_file)
        index  = load_json(idx_file, [])
        return matrix, index
    return np.zeros((0, dims), dtype=np.float32), []

def save_embeddings(matrix: np.ndarray, index: list, emb_file: str, idx_file: str):
    np.save(emb_file, matrix.astype(np.float32))
    save_json(index, idx_file)

# ── EMBEDDING API ─────────────────────────────────────────────────────────────

def embed_texts(client: OpenAI, texts: list, model: str = EMBED_MODEL) -> list:
    """Embed a batch of texts. Retries up to 3 times on transient errors."""
    for attempt in range(3):
        try:
            response    = client.embeddings.create(model=model, input=texts)
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in sorted_data]
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"\n  [!] Embedding error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                print(f"      Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

# ── PHASE 1 — CV WORK PROFILE EXTRACTION ─────────────────────────────────────

def extract_cv_work_profile(client: OpenAI, force: bool = False) -> str:
    """
    Extract activity-prose work profile from RESUME_TEXT using GPT-4o-mini.
    Result is cached in cv_work_profile.txt — edit it freely after generation.
    Re-runs only if file missing or --re-extract-cv flag passed.
    """
    if os.path.exists(CV_WORK_PROFILE_FILE) and not force:
        with open(CV_WORK_PROFILE_FILE, "r", encoding="utf-8") as f:
            cached = f.read().strip()
        if cached:
            print(f"  [OK] CV work profile loaded from cache.")
            print(f"       Edit {CV_WORK_PROFILE_FILE} to adjust if needed.\n")
            return cached

    print(f"  Extracting CV work profile via {CHAT_MODEL}...", end=" ", flush=True)
    prompt = CV_EXTRACTION_PROMPT.format(
        resume_text=RESUME_TEXT.strip()[:MAX_PROFILE_CHARS]
    )

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=200,
    )
    profile = response.choices[0].message.content.strip()
    print("OK\n")

    # Save for inspection — user can edit before embedding
    with open(CV_WORK_PROFILE_FILE, "w", encoding="utf-8") as f:
        f.write(profile)

    print(f"  Saved to: {CV_WORK_PROFILE_FILE}")
    print(f"  ── CV WORK PROFILE ──")
    print(f"  {profile}\n")
    print(f"  Review the above. Edit the file if anything looks wrong.")
    print(f"  Then re-run with --cv-only to re-embed, or continue.\n")

    return profile


# ── PHASE 2 — JOB WORK PROFILE EMBEDDING ─────────────────────────────────────

def embed_work_profiles(client: OpenAI, work_profiles: dict):
    """
    Incrementally embed job work profiles from work_profiles.json.
    Skips URLs already in work_embed_index_large.json.
    """
    matrix, index = load_existing_embeddings(WORK_EMB_FILE, WORK_IDX_FILE, EMBED_DIMS)
    indexed_urls  = set(index)

    # Only embed non-empty profiles not yet indexed
    to_embed = [
        (url, text) for url, text in work_profiles.items()
        if url not in indexed_urls and text and text.strip()
    ]

    skipped_empty = sum(
        1 for url, text in work_profiles.items()
        if url not in indexed_urls and not (text and text.strip())
    )

    if not to_embed:
        print(f"  [OK] All job work profiles already embedded ({len(index)} indexed).")
        if skipped_empty:
            print(f"       {skipped_empty} empty profiles skipped (no text extracted).")
        return matrix, index

    total   = len(to_embed)
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    print(f"  Job profiles to embed : {total}")
    print(f"  Already indexed       : {len(index)}")
    if skipped_empty:
        print(f"  Skipped (empty)       : {skipped_empty}")
    print(f"  Batches               : {batches}  ({BATCH_SIZE}/batch)")
    print(f"  Model                 : {EMBED_MODEL}  ({EMBED_DIMS} dims)")
    print()

    new_vectors = []
    new_urls    = []

    for batch_i in range(batches):
        batch = to_embed[batch_i * BATCH_SIZE : (batch_i + 1) * BATCH_SIZE]
        urls  = [item[0] for item in batch]
        texts = [item[1] for item in batch]

        print(f"  Batch {batch_i+1:>3}/{batches}  ({len(texts)} profiles)...",
              end=" ", flush=True)
        vectors = embed_texts(client, texts)
        new_vectors.extend(vectors)
        new_urls.extend(urls)
        print("OK")

    if not new_vectors:
        print("  No new vectors produced.")
        return matrix, index

    new_matrix  = np.array(new_vectors, dtype=np.float32)
    full_matrix = np.vstack([matrix, new_matrix]) if matrix.shape[0] > 0 else new_matrix
    full_index  = index + new_urls

    save_embeddings(full_matrix, full_index, WORK_EMB_FILE, WORK_IDX_FILE)

    print(f"\n  New vectors  : {len(new_vectors)}")
    print(f"  Total matrix : {full_matrix.shape}")
    print(f"  Saved        : {WORK_EMB_FILE}")

    return full_matrix, full_index


# ── PHASE 3 — CV WORK PROFILE EMBEDDING ──────────────────────────────────────

def embed_cv_work_profile(client: OpenAI, cv_profile_text: str):
    """
    Embed the CV work profile text → profile_work_embedding_large.npy
    Single vector, shape (3072,).
    """
    print(f"\n  Embedding CV work profile...", end=" ", flush=True)
    vectors = embed_texts(client, [cv_profile_text])
    vec     = np.array(vectors[0], dtype=np.float32)
    np.save(PROFILE_WORK_EMB_FILE, vec)
    print(f"OK")
    print(f"  Shape  : {vec.shape}")
    print(f"  Saved  : {PROFILE_WORK_EMB_FILE}")
    return vec


# ── STATUS ────────────────────────────────────────────────────────────────────

def print_status(work_profiles: dict):
    matrix, index = load_existing_embeddings(WORK_EMB_FILE, WORK_IDX_FILE, EMBED_DIMS)
    cv_exists     = os.path.exists(CV_WORK_PROFILE_FILE)
    cv_emb_exists = os.path.exists(PROFILE_WORK_EMB_FILE)

    total_profiles = len(work_profiles)
    non_empty      = sum(1 for t in work_profiles.values() if t and t.strip())
    indexed        = len(index)
    remaining      = non_empty - indexed

    print(f"\n{'═'*58}")
    print(f"  WORK EMBEDDING STATUS")
    print(f"{'═'*58}")
    print(f"  work_profiles.json   : {total_profiles} entries  ({non_empty} non-empty)")
    print(f"  Job embeddings       : {indexed} indexed  "
          f"({remaining} remaining)  shape={matrix.shape}")
    print(f"  Embedding file       : {'EXISTS' if matrix.shape[0]>0 else 'NOT FOUND'}  "
          f"→ {WORK_EMB_FILE}")
    print(f"  Index file           : {'EXISTS' if index else 'NOT FOUND'}  "
          f"→ {WORK_IDX_FILE}")
    print(f"\n  CV work profile text : {'EXISTS' if cv_exists else 'NOT FOUND'}  "
          f"→ {CV_WORK_PROFILE_FILE}")
    print(f"  CV work embedding    : {'EXISTS' if cv_emb_exists else 'NOT FOUND'}  "
          f"→ {PROFILE_WORK_EMB_FILE}")

    if cv_exists:
        with open(CV_WORK_PROFILE_FILE, "r", encoding="utf-8") as f:
            cv_text = f.read().strip()
        print(f"\n  CV work profile preview:")
        print(f"  {cv_text[:300]}{'...' if len(cv_text)>300 else ''}")

    print(f"{'═'*58}\n")


# ── COSINE PREVIEW — sanity check before scoring ──────────────────────────────

def cosine_preview(work_profiles: dict, top_n: int = 10):
    """
    Load work embeddings and profile embedding, compute cosine for all jobs,
    print top N and bottom N. Use this to validate the threshold before scoring.
    """
    if not os.path.exists(WORK_EMB_FILE) or not os.path.exists(PROFILE_WORK_EMB_FILE):
        print("[!] Run full pipeline first before previewing cosine scores.")
        return

    matrix    = np.load(WORK_EMB_FILE)
    index     = load_json(WORK_IDX_FILE, [])
    prof_vec  = np.load(PROFILE_WORK_EMB_FILE)

    # Load job metadata for display
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        db = {}
        if isinstance(data, list):
            db = {j.get("apply_url",""): j for j in data}
        else:
            db = data
    else:
        db = {}

    # Normalise and compute cosine
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    prof_norm   = prof_vec / (np.linalg.norm(prof_vec) + 1e-9)
    scores      = matrix_norm @ prof_norm   # shape (N,)

    ranked = sorted(zip(scores, index), reverse=True)

    def print_block(label, items):
        print(f"\n  {label}")
        print(f"  {'─'*60}")
        for score, url in items:
            job     = db.get(url, {})
            company = job.get("company", "?").upper()
            title   = job.get("title", url[:60])
            wp      = work_profiles.get(url, "")[:120]
            print(f"  {score:.4f}  [{company}] {title}")
            print(f"           {wp}...")
            print()

    print(f"\n{'═'*62}")
    print(f"  COSINE PREVIEW — work profile vs all jobs")
    print(f"  Total jobs: {len(ranked)}")
    cutoffs = [0.70, 0.65, 0.60, 0.55, 0.50, 0.45]
    for c in cutoffs:
        n = sum(1 for s, _ in ranked if s >= c)
        pct = 100 * n / len(ranked) if ranked else 0
        print(f"  cosine ≥ {c:.2f} : {n:>5} jobs  ({pct:.1f}% of DB)")
    print(f"{'═'*62}")

    print_block(f"TOP {top_n} (most similar to your work profile)", ranked[:top_n])
    print_block(f"BOTTOM {top_n} (least similar)", ranked[-top_n:])


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build work profile embeddings for JobRadar cosine pre-filter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python build_work_embeddings.py                  # full run (all 3 phases)
  python build_work_embeddings.py --status         # show state, no API calls
  python build_work_embeddings.py --cv-only        # re-extract CV + re-embed CV only
  python build_work_embeddings.py --embed-only     # skip CV extraction, embed jobs only
  python build_work_embeddings.py --re-extract-cv  # force re-extract CV profile text
  python build_work_embeddings.py --preview        # cosine scores vs all jobs (post-run)
  python build_work_embeddings.py --preview --top 20  # show top 20
        """
    )
    parser.add_argument("--status",       action="store_true",
                        help="Show pipeline state, no API calls.")
    parser.add_argument("--cv-only",      action="store_true",
                        help="Re-extract CV work profile and re-embed it only.")
    parser.add_argument("--embed-only",   action="store_true",
                        help="Skip CV extraction, run job embedding + CV embedding.")
    parser.add_argument("--re-extract-cv", action="store_true",
                        help="Force re-extract CV profile even if file exists.")
    parser.add_argument("--preview",      action="store_true",
                        help="Print cosine score distribution and top/bottom jobs.")
    parser.add_argument("--top",          type=int, default=10,
                        help="Number of top/bottom jobs to show in --preview (default 10).")
    args = parser.parse_args()

    work_profiles = load_json(WORK_PROFILES_FILE, {})

    if not work_profiles and not args.status:
        print(f"[!] work_profiles.json not found or empty:\n    {WORK_PROFILES_FILE}")
        print(f"    Run extract_work_profiles.py first.")
        sys.exit(1)

    if args.status:
        print_status(work_profiles)
        return

    if args.preview:
        cosine_preview(work_profiles, top_n=args.top)
        return

    # Resolve API key
    api_key = OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("❌  No API key found. Set OPENAI_API_KEY env var or paste in script.")
        sys.exit(1)
    client = OpenAI(api_key=api_key)

    print(f"\n{'═'*58}")
    print(f"  JobRadar — Work Profile Embedding Pipeline")
    print(f"  work_profiles : {len(work_profiles)} entries")
    print(f"  Embed model   : {EMBED_MODEL}  ({EMBED_DIMS} dims)")
    print(f"{'═'*58}\n")

    # Phase 1 — CV extraction
    if not args.embed_only:
        print("-- Phase 1: CV work profile extraction -----------------------\n")
        cv_profile = extract_cv_work_profile(client, force=args.re_extract_cv or args.cv_only)
    else:
        # Load from cache — must exist
        if not os.path.exists(CV_WORK_PROFILE_FILE):
            print(f"[!] cv_work_profile.txt not found. Run without --embed-only first.")
            sys.exit(1)
        with open(CV_WORK_PROFILE_FILE, "r", encoding="utf-8") as f:
            cv_profile = f.read().strip()
        print(f"-- Phase 1: Skipped (using cached cv_work_profile.txt) -------\n")

    if not cv_profile:
        print("[!] CV work profile is empty — cannot embed. Check extraction.")
        sys.exit(1)

    # Phase 2 — job embeddings (skip if --cv-only)
    if not args.cv_only:
        print("-- Phase 2: Job work profile embedding -----------------------\n")
        embed_work_profiles(client, work_profiles)
        print()

    # Phase 3 — CV embedding
    print("-- Phase 3: CV work profile embedding ------------------------\n")
    embed_cv_work_profile(client, cv_profile)

    print(f"\n{'═'*58}")
    print(f"  Done.")
    print(f"{'═'*58}\n")

    print_status(work_profiles)

    print("  Next step: run --preview to validate cosine threshold,")
    print("  then add pre-filter to score_jobs_openai.py.\n")


if __name__ == "__main__":
    main()