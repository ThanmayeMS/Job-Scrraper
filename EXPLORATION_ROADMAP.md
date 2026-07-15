# JobRadar — Exploration Roadmap

**Next phase topics to build out. Each section has context, decisions made, and exact next steps.**

---

## 1. Selenium-Wire — Autonomous Company Investigation

### What it is
`selenium-wire` is a drop-in replacement for Selenium that captures all HTTP traffic the browser makes — exactly what you see in the Network tab, but programmatically. This is the foundation for the autonomous "add company" agent.

### Why it matters
Right now adding a new company requires you to manually open DevTools, find the XHR calls, inspect elements, and report findings. selenium-wire lets a script do all of that automatically.

### Install
```bash
pip install selenium-wire
```

### Basic usage
```python
from seleniumwire import webdriver  # instead of selenium.webdriver
import json

driver = webdriver.Chrome()
driver.get("https://careers.hsbc.com/india")

import time; time.sleep(4)  # let page fully load

# All network requests captured automatically
for request in driver.requests:
    if request.response:
        ct = request.response.headers.get("Content-Type", "")
        if "json" in ct:
            print(request.url)
            print(request.method)
            try:
                body = json.loads(request.response.body)
                print(str(body)[:300])
            except:
                pass
```

### What to build: `investigator.py`
Full implementation discussed — see agent section below.

### Known gotcha
selenium-wire adds overhead to every request (it proxies traffic). Some sites detect this. If a site that normally works with regular Selenium breaks with selenium-wire, use regular Selenium for scraping and selenium-wire only for investigation.

---

## 2. Cron Setup — Scheduled Cloud Scraping

### Architecture
```
DigitalOcean Droplet ($12/mo, 2GB RAM Ubuntu 22.04)
  → Chrome + ChromeDriver installed
  → Xvfb virtual display (makes Chrome think it has a real screen)
  → cron schedules daily runs
  → you sync results to local machine
```

### Server setup steps
```bash
# 1. Provision Ubuntu 22.04 droplet on DigitalOcean

# 2. Install Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb

# 3. Install Xvfb (virtual display — beats bot detection better than headless)
sudo apt-get install xvfb
pip install pyvirtualdisplay

# 4. Install Python deps
pip install selenium selenium-wire requests beautifulsoup4 openai anthropic filelock

# 5. Set env vars
echo 'export OPENAI_API_KEY="your_key"' >> ~/.bashrc
source ~/.bashrc

# 6. Upload code
scp -r jobradar/ root@your_server_ip:/home/ubuntu/
```

### Cron schedule
```bash
crontab -e

# Start virtual display on boot
@reboot Xvfb :99 -screen 0 1400x900x24 &

# Run scraper daily at 7am
0 7 * * * cd /home/ubuntu/jobradar && DISPLAY=:99 python -m job_scraper_pkg --parallel >> logs/scraper_$(date +\%Y\%m\%d).log 2>&1

# Run scorer daily at 8am (after scraper)
0 8 * * * cd /home/ubuntu/jobradar && python score_jobs_openai.py --batch 500 >> logs/scorer_$(date +\%Y\%m\%d).log 2>&1
```

### Sync script (run locally to pull latest)
```bash
# sync.sh
#!/bin/bash
SERVER="root@your_server_ip"
LOCAL="./jobradar"

echo "Pulling latest DB..."
scp $SERVER:/home/ubuntu/jobradar/jobs_db.json $LOCAL/
scp $SERVER:/home/ubuntu/jobradar/jobs_recent.json $LOCAL/

echo "Uploading tracker..."
scp $LOCAL/jobradar_tracker.json $SERVER:/home/ubuntu/jobradar/

echo "Done. Open job_viewer.html"
```

### `core/browser.py` change for cloud
Auto-detect whether a display is available:
```python
import os

def get_browser(headless: bool = None) -> webdriver.Chrome:
    if headless is None:
        headless = os.environ.get("DISPLAY") is None

    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)
```

Same code works locally (opens window) and on server (runs headless/Xvfb).

### Difficulty: one focused weekend

---

## 3. Embeddings — Scalable Scoring Architecture

### The problem
Current approach: score every job with LLM call.
- 8,000 jobs × $0.002 = $16 per full rescore
- At 80,000 jobs: $160, ~53 minutes
- At 1,000 users × 80,000 jobs: $160,000 — not viable

### The solution: 2-stage retrieval
```
Stage 1 — Recall (embeddings, fast, cheap, approximate):
  Find top 150 most relevant jobs from 80,000
  Cost: fraction of a cent
  Time: milliseconds

Stage 2 — Rank (LLM, slow, expensive, precise):
  Score top 150 only
  Cost: ~$0.30 per user
  Time: ~1 minute
```

### How embeddings work
An embedding model converts text to a vector (list of ~1536 numbers) that captures semantic meaning. Similar text → vectors that point in the same direction → high cosine similarity score.

```python
from openai import OpenAI
client = OpenAI()

def embed(text: str) -> list[float]:
    response = client.embeddings.create(
        model="text-embedding-3-small",  # $0.00002 per 1K tokens
        input=text[:8000]
    )
    return response.data[0].embedding
```

Cost: 8,000 jobs × ~500 tokens × $0.00002/1K = **$0.08 total**. One-time per job.

### Implementation plan

**Step 1 — Embed jobs at insert time (add to `core/db.py`)**
```python
# In upsert_job(), after inserting:
from openai import OpenAI
_embed_client = OpenAI()

def _embed_job(job: dict) -> list:
    text = " ".join(filter(None, [
        job.get("title", ""),
        job.get("about_the_job", "") or job.get("job_description", ""),
        job.get("responsibilities", ""),
        job.get("minimum_qualifications", ""),
    ]))[:4000]
    try:
        resp = _embed_client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return resp.data[0].embedding
    except:
        return []
```

**Step 2 — Similarity search (pure numpy, no new infrastructure)**
```python
import numpy as np

def find_top_matches(cv_embedding: list, db: dict, top_n: int = 150) -> list:
    jobs = [j for j in db.values() if j.get("embedding")]
    if not jobs:
        return list(db.values())  # fallback: return all if no embeddings yet

    matrix = np.array([j["embedding"] for j in jobs])
    cv_vec = np.array(cv_embedding)

    # Cosine similarity — vectorised, instant for 80k jobs
    similarities = matrix @ cv_vec / (
        np.linalg.norm(matrix, axis=1) * np.linalg.norm(cv_vec) + 1e-10
    )

    top_indices = np.argsort(similarities)[::-1][:top_n]
    return [jobs[i] for i in top_indices]
```

**Step 3 — CV upload + embed**
```python
import pdfplumber

def extract_cv_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

def process_cv(pdf_path: str) -> list:
    text = extract_cv_text(pdf_path)
    return embed(text)
```

**Step 4 — Wire into scorer**
```python
# score_jobs_openai.py — change to_score selection:
cv_embedding = process_cv("my_resume.pdf")
top_matches  = find_top_matches(cv_embedding, db, top_n=150)
to_score     = [j for j in top_matches if "similarity_score" not in j]
# Rest of scoring logic unchanged
```

### Recall experiment (run before building)
You already have scored data. Verify the approach works before committing:

```python
import json, numpy as np

db         = json.load(open("jobs_db.json"))
scored_7   = [j for j in db if j.get("similarity_score", 0) >= 7]
all_jobs   = [j for j in db if j.get("embedding")]

# Embed your CV
cv_embedding = embed(open("my_resume.txt").read())

# Get top 150
top_150    = find_top_matches(cv_embedding, {j["apply_url"]:j for j in all_jobs}, 150)
top_urls   = {j["apply_url"] for j in top_150}

# Check recall
found      = sum(1 for j in scored_7 if j["apply_url"] in top_urls)
print(f"Recall: {found}/{len(scored_7)} high-scoring jobs in top 150")
# Target: 95%+. If lower, increase top_n to 200.
```

### For product scale: pgvector
```sql
-- One-time setup
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE jobs ADD COLUMN embedding vector(1536);

-- Query: top 150 matches for a CV
SELECT id, title, company,
       1 - (embedding <=> '[cv_vector]') AS similarity
FROM jobs
ORDER BY embedding <=> '[cv_vector]'
LIMIT 150;
```

### Cost comparison
| Scenario | Current | With embeddings |
|---|---|---|
| Full rescore (new user) | $160, 53 min | $0.30, 1 min |
| Daily refresh (50 new jobs) | $0.10 | $0.01 |
| 1,000 users daily | $100/day | $2/day |

### Build order
```
Week 1: Add embedding to scraper (embed at insert time)
Week 2: CV extraction (pdfplumber + embed)
Week 3: Similarity search (numpy)
Week 4: Wire into scorer
Week 5: Run recall experiment, tune top_n
```

---

## 4. RAG Model — Conversational Interface

### What it is
A chat interface where you can ask questions about your scored job database in natural language.

```
"Show me Goldman Sachs roles where Python is not required"
"Which companies have the most data engineering roles scoring 7+?"
"Compare the top 3 matches for my profile"
"What skills am I missing most consistently across rejections?"
"Which of my saved jobs have the highest match scores?"
```

### Why it's different from search
Search returns a list. RAG returns an answer. The LLM reads retrieved jobs and responds conversationally — it can compare, summarise, reason across multiple jobs at once.

### Architecture
```
User question
    ↓
Embed the question
    ↓
Vector search → retrieve top 20 relevant jobs
    ↓
Pass jobs + question to Claude
    ↓
Claude answers conversationally
```

### Implementation

**System prompt:**
```python
SYSTEM = """
You are a job search assistant. The user has uploaded their CV.
Their scored job matches are provided below.

Answer questions about these jobs, help them decide which to prioritise,
surface patterns, and give honest assessments based on the match scores
and gap analysis.

Be direct and specific. Reference job titles and companies by name.
If asked to compare, compare. If asked for a recommendation, give one.

SCORED JOBS (sorted by match score):
{jobs_context}

USER PROFILE SUMMARY:
{cv_summary}
"""
```

**RAG query function:**
```python
import anthropic

client = anthropic.Anthropic()

def rag_query(question: str, cv_embedding: list, db: dict) -> str:
    # Retrieve relevant jobs
    relevant_jobs = find_top_matches(cv_embedding, db, top_n=20)

    # Format as context
    jobs_context = "\n\n".join([
        f"[{j.get('similarity_score', 'N/A')}/10] {j['title']} @ {j['company']}\n"
        f"Location: {j.get('locations', '')}\n"
        f"Match reason: {j.get('match_reason', '')}\n"
        f"Gaps: {j.get('match_gaps', '')}\n"
        f"Apply: {j['apply_url']}"
        for j in relevant_jobs
    ])

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=SYSTEM.format(
            jobs_context=jobs_context,
            cv_summary="Senior data analyst, 5 years at AmEx, strong in SQL/Python/Tableau"
        ),
        messages=[{"role": "user", "content": question}]
    )

    return response.content[0].text

# Usage
answer = rag_query(
    "Which 3 jobs should I apply to first and why?",
    cv_embedding,
    db
)
print(answer)
```

### Cost per RAG query
- 1 embedding call: ~$0.0001
- 1 Claude call (20 jobs as context): ~$0.005
- **Total: ~$0.005 per question** — negligible

### Where it lives in the product
Two options:

**Option A — In the viewer (chat panel in the sidebar)**
Click a chat icon → drawer slides out with a text input → ask questions → Claude responds inline. Stays in the single HTML file using the Claude API directly from the browser.

**Option B — Separate `rag.py` CLI script**
```bash
python rag.py
> Which Goldman Sachs roles match my profile?
> Compare the top Microsoft and Citi roles
> What skill should I add to my resume to get more 9/10 scores?
```

Option B is faster to build and more useful for personal use. Option A is better for a product.

### Build order
```
Phase 1 (after embeddings): CLI version
  rag.py — load db, embed query, retrieve, ask Claude, print answer

Phase 2 (product): Viewer integration
  Add chat panel to job_viewer.html using Claude API in browser
```

---

## 5. Autonomous Agent — Add Company Without Human

### What it is
One command adds a new company to your scraper. The agent investigates the site, identifies the platform, writes the scraper, tests it, and registers it — no human in the loop for known platforms.

```bash
python add_company.py "HSBC" "https://careers.hsbc.com/india"
# → investigates → writes → tests → fixes → done
```

### Files to create
```
add_company.py    ← orchestrator (one command entry point)
investigator.py   ← selenium-wire investigation
agent.py          ← Claude API writes the scraper
executor.py       ← run + test + fix loop
```

### `investigator.py`
```python
from seleniumwire import webdriver
from bs4 import BeautifulSoup
import json, time

def investigate(url: str) -> dict:
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    driver.get(url)
    time.sleep(4)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)

    # Capture JSON XHR calls
    api_calls = []
    for req in driver.requests:
        if not req.response: continue
        ct = req.response.headers.get("Content-Type", "")
        if "json" not in ct: continue
        try:
            body = json.loads(req.response.body)
            api_calls.append({
                "url":     req.url,
                "method":  req.method,
                "status":  req.response.status_code,
                "preview": str(body)[:500],
            })
        except: continue

    # Job card hints from DOM
    soup = BeautifulSoup(driver.page_source, "html.parser")
    job_hints = []
    for selector in ["a[href*='/job/']", "a[href*='/jobs/']",
                     "[class*='job-item']", "[class*='job-card']", "[data-job-id]"]:
        els = soup.select(selector)
        if els:
            job_hints.append({
                "selector": selector,
                "count":    len(els),
                "sample":   str(els[0])[:200],
            })

    # Pagination hints — click next, watch what fires
    pagination_hints = []
    try:
        before = len(driver.requests)
        next_btn = driver.find_element("css selector", "a.next, [aria-label='Next']")
        next_btn.click()
        time.sleep(2)
        for req in driver.requests[before:]:
            if req.response and "json" in req.response.headers.get("Content-Type", ""):
                pagination_hints.append(req.url)
    except: pass

    driver.quit()
    return {
        "url":              url,
        "api_calls":        api_calls,
        "job_hints":        job_hints,
        "pagination_hints": pagination_hints,
        "has_xhr":          len(api_calls) > 0,
    }
```

### `agent.py`
```python
import anthropic, json

client = anthropic.Anthropic()

def run_agent(company: str, findings: dict) -> str:
    arch_doc     = open("SCRAPER_ARCHITECTURE.md").read()
    amazon_ref   = open("job_scraper_pkg/scrapers/amazon.py").read()
    jpmorgan_ref = open("job_scraper_pkg/scrapers/jpmorgan.py").read()
    citi_ref     = open("job_scraper_pkg/scrapers/citi.py").read()
    base_ref     = open("job_scraper_pkg/base.py").read()
    db_ref       = open("job_scraper_pkg/core/db.py").read()

    prompt = f"""
You are an expert web scraper. Write a new scraper plugin for {company}.

## Contract (BaseScraper)
{base_ref}

## DB helpers
{db_ref}

## Architecture guide
{arch_doc}

## Reference: Amazon (pure requests)
{amazon_ref}

## Reference: JPMorgan (requests + concurrent detail)
{jpmorgan_ref}

## Reference: Citi (Selenium 2-pass)
{citi_ref}

## Investigation findings for {company}
{json.dumps(findings, indent=2)}

Write scrapers/{company.lower()}.py following the exact same patterns.
Return ONLY Python code. No explanation. No markdown fences.
"""
    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text
```

### `executor.py`
```python
import subprocess, json, os

def test_scraper(company_key: str) -> dict:
    result = subprocess.run(
        ["python", "-m", "job_scraper_pkg", "--companies", company_key],
        capture_output=True, text=True, timeout=180
    )
    db = json.load(open("jobs_db.json")) if os.path.exists("jobs_db.json") else []
    jobs = [j for j in db if j.get("company","").lower().replace(" ","") == company_key]
    return {
        "stdout":     result.stdout,
        "stderr":     result.stderr[-2000:],
        "jobs_found": len(jobs),
        "sample":     jobs[:3],
        "success":    len(jobs) >= 5,
    }

def fix_scraper(company: str, code: str, error: dict) -> str:
    import anthropic
    client = anthropic.Anthropic()
    prompt = f"""
Fix this scraper for {company}.

## Current code:
{code}

## Test output:
stdout: {error['stdout'][-2000:]}
stderr: {error['stderr']}
jobs found: {error['jobs_found']}
sample: {json.dumps(error['sample'], indent=2)}

Common fixes:
- 0 jobs: wrong pagination field, wrong country filter, missing auth
- Empty fields: wrong selectors or field names
- Exception: read stderr traceback and fix

Return ONLY the fixed Python code.
"""
    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text
```

### `add_company.py` (orchestrator)
```python
import sys, os, json
from investigator import investigate
from agent import run_agent
from executor import test_scraper, fix_scraper

def register(company: str, code: str):
    key       = company.lower().replace(" ", "")
    classname = "".join(w.capitalize() for w in company.split()) + "Scraper"
    filepath  = f"job_scraper_pkg/scrapers/{key}.py"

    with open(filepath, "w") as f: f.write(code)
    print(f"  ✓ {filepath}")

    # Add to scrapers/__init__.py
    init = open("job_scraper_pkg/scrapers/__init__.py").read()
    imp  = f"from .{key} import {classname}"
    reg  = f'    "{key}": {classname},'
    if imp not in init:
        lines = init.split("\n")
        last  = max(i for i,l in enumerate(lines) if l.startswith("from ."))
        lines.insert(last + 1, imp)
        init  = "\n".join(lines)
    if reg not in init:
        init  = init.replace("}", f"{reg}\n}}")
    with open("job_scraper_pkg/scrapers/__init__.py", "w") as f: f.write(init)
    print(f"  ✓ Registered in __init__.py")

    # Add to COMPANIES_TO_RUN in __main__.py
    main = open("job_scraper_pkg/__main__.py").read()
    if f'"{key}"' not in main:
        main = main.replace('"citi",\n]', f'"citi",\n    "{key}",\n]')
        with open("job_scraper_pkg/__main__.py", "w") as f: f.write(main)
    print(f"  ✓ Added to COMPANIES_TO_RUN")

def add_company(company: str, url: str):
    print(f"\n{'='*60}\n  Adding: {company}\n  URL: {url}\n{'='*60}")
    key = company.lower().replace(" ", "")

    print("\n[1/4] Investigating...")
    findings = investigate(url)
    print(f"  API calls: {len(findings['api_calls'])} | Job hints: {len(findings['job_hints'])} | Has XHR: {findings['has_xhr']}")

    print("\n[2/4] Generating scraper...")
    code = run_agent(company, findings)
    print(f"  {len(code.splitlines())} lines generated")

    print("\n[3/4] Registering...")
    register(company, code)

    print("\n[4/4] Testing (up to 3 attempts)...")
    for attempt in range(1, 4):
        print(f"  Attempt {attempt}/3...")
        result = test_scraper(key)
        if result["success"]:
            print(f"\n  ✅ {result['jobs_found']} jobs found")
            if result["sample"]:
                print(f"  Sample: {result['sample'][0].get('title')} @ {result['sample'][0].get('company')}")
            return
        print(f"  ✗ {result['jobs_found']} jobs. Fixing...")
        code = fix_scraper(company, code, result)
        with open(f"job_scraper_pkg/scrapers/{key}.py", "w") as f: f.write(code)

    print(f"\n  ⚠️  Needs manual review: job_scraper_pkg/scrapers/{key}.py")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python add_company.py 'Company Name' 'https://careers.url'")
        sys.exit(1)
    add_company(sys.argv[1], sys.argv[2])
```

### Usage
```bash
python add_company.py "HSBC" "https://careers.hsbc.com/india"
python add_company.py "Deutsche Bank" "https://careers.db.com"
python add_company.py "Barclays" "https://search.jobs.barclays"
```

### Success rates by platform
| Platform | 1st attempt | After fixes |
|---|---|---|
| Oracle HCM | 80% | 99% |
| SmartRecruiters | 85% | 99% |
| Greenhouse | 90% | 100% |
| Lever | 90% | 100% |
| Workday | 60% | 90% |
| Custom API | 50% | 85% |
| Phenom SSR | 40% | 80% |

### Install
```bash
pip install selenium-wire anthropic
```

---

## 6. Multi-Terminal Parallel Selenium (Parked)

Discussed but parked. The idea: run Google, Mastercard, Citi in separate terminals simultaneously.

Requires `filelock` for cross-process DB safety:
```bash
pip install filelock
```

Change in `core/db.py`:
```python
from filelock import FileLock
_file_lock = FileLock("jobs_db.lock")

def upsert_job(db, job):
    with _file_lock:   # cross-process lock
        with _db_lock:  # cross-thread lock
            # existing logic
```

Run:
```bash
# Terminal 1
python -m job_scraper_pkg --companies google

# Terminal 2
python -m job_scraper_pkg --companies citi

# Terminal 3
python -m job_scraper_pkg --companies mastercard
```

Speedup: 65 min sequential → ~30 min parallel (limited by slowest scraper).

**Status: Parked. Implement after cloud setup is working.**

---

## 7. Tailoring Feature (Discussed, Not Built)

### The full pipeline
```
PDF in → extract text → Claude parses → structured resume JSON
    ↓
Job JD + resume JSON → Claude generates tailored content
    ↓
WeasyPrint renders → tailored PDF out
```

### For .docx uploads (surgical edits, format preserved)
```python
from docx import Document

doc = Document("resume.docx")
for para in doc.paragraphs:
    if is_summary_section(para):
        para.runs[0].text = new_summary
doc.save("resume_tailored.docx")
```

### For PDF uploads (extract + re-render with template)
```python
# Extract
import pdfplumber
text = "\n".join(p.extract_text() for p in pdfplumber.open("resume.pdf").pages)

# Claude parses into structured JSON
# Claude generates tailored version
# WeasyPrint renders HTML template → PDF

from weasyprint import HTML
HTML(string=render_template("resume.html", data=tailored)).write_pdf("out.pdf")
```

### Tailoring prompt structure
```
Given this resume and job description:
1. Rewrite summary (3-4 lines) mirroring JD language
2. Which 3 bullet points to surface first per role for this JD
3. Skills candidate has but didn't list that should be added
4. One cover letter paragraph addressing the main gap

Return JSON: {summary, bullet_reorders, skills_to_add, gap_paragraph}
```

### Install
```bash
pip install python-docx pdfplumber weasyprint
```

**Status: Architecture designed, not built. Build after embedding pipeline.**

---

## Build Priority Order

```
1. Cron + cloud setup          ← infrastructure, enables everything else
2. Embeddings (Steps 1-4)      ← solves cost + speed, needed before product
3. Run recall experiment        ← validate before committing
4. RAG CLI version             ← low effort, high value, builds on embeddings
5. Tailoring feature           ← highest user value, 5 week build
6. Autonomous agent            ← selenium-wire + Claude agent, 4 week build
7. Multi-terminal Selenium     ← nice to have, implement last
```

---

## Product Vision (Discussed)

Target: senior professionals (5-10 years, ₹30-50 LPA) targeting India's top tech/finance companies.

What you have that competitors don't:
- Semantic scoring with gap analysis (not keyword matching)
- Data direct from source (8 companies, fresh daily)
- Architecture that scales to more companies via agent

What's needed for product:
- Backend API (FastAPI, ~2-3 weeks)
- User auth (Supabase, ~3 days)
- CV upload + per-user scoring (embedding pipeline is the foundation)
- Notification layer (daily email with new top matches)
- Hosted DB (Supabase/Postgres + pgvector)

Monetisation: freemium — top 10 matches free, full feed + tailoring paid (₹2,000/month).

Realistic timeline solo: 3 months to working MVP, 5-6 months to something worth charging for.

The hardest part is not the technology — it's getting 10 strangers to use it and tell you what's broken.

---

*Last updated: March 2026*
*Status: Personal use working (5,299 jobs, 8 companies). Next: cloud scheduling + embeddings.*
