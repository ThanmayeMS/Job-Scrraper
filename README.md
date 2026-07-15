# JobRadar

Multi-user job aggregation, semantic matching, and LLM fit-scoring platform.

JobRadar scrapes jobs directly from company career sites, embeds them for cheap
semantic recall, and uses an LLM to score how well each job fits **each user's** CV.
This repo is the productionized evolution of an earlier single-user script collection:
a real backend service, a real database, background workers, auth, containers, and CI.

---

## Architecture

```
                         ┌─────────────────────────────┐
   Company career sites  │  Celery workers (+ beat)     │
   (Amazon, Google, …) ──▶  • scrape_company_task       │
                         │  • embed_new_jobs_task        │
                         │  • score_user_task            │
                         └──────────────┬───────────────┘
                                        │  SQLAlchemy
                                        ▼
   Browser / API client   ┌─────────────────────────────┐
        │  JWT             │  PostgreSQL + pgvector       │
        ▼                  │  users · jobs · embeddings   │
   ┌──────────────┐  HTTP  │  job_scores · trackers       │
   │ FastAPI API  │◀──────▶│                              │
   │ /api/*       │        └─────────────────────────────┘
   └──────────────┘
        ▲   Redis (broker/result) ◀── Celery
        │
   Minimal static frontend (/app)
```

**Two-stage matching** (from the original roadmap, now real):
1. **Recall** — embed the user's CV, pull the top-N nearest jobs via pgvector cosine
   distance (cheap, milliseconds).
2. **Rank** — LLM-score only those candidates against the CV (expensive, accurate).

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI + Uvicorn |
| Auth | OAuth2 password flow, JWT (python-jose), bcrypt (passlib) |
| Database | PostgreSQL 16 + **pgvector** |
| ORM / migrations | SQLAlchemy 2.0 (sync) + Alembic |
| Background jobs | Celery + Redis, Celery Beat for daily schedule |
| Scraping | requests + BeautifulSoup (API sites), Selenium (JS sites) |
| LLM | OpenAI — `gpt-4o-mini` (scoring) + `text-embedding-3-small` (embeddings) |
| Frontend | single static HTML page (intentionally minimal) |
| Tooling | Ruff, mypy, pytest, pre-commit, GitHub Actions, Docker Compose |

## Quick start (Docker)

```bash
cp .env.example .env          # set JWT_SECRET_KEY and OPENAI_API_KEY
docker compose up -d --build  # api + worker + beat + postgres + redis
# migrations run automatically on api start
```

- Web UI:  http://localhost:8000/  (the dashboard)
- API docs: http://localhost:8000/docs
- Health:  http://localhost:8000/health

Seed a demo admin + sample jobs (optional):

```bash
docker compose exec api python scripts/seed_demo.py
```

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
make install                  # editable install + pre-commit hooks
# bring up just infra:
docker compose up -d db redis
make migrate                  # alembic upgrade head
make api                      # uvicorn with reload
make worker                   # celery worker (separate shell)
make beat                     # celery beat  (separate shell)
```

## Core API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | — | Create account |
| POST | `/api/auth/login` | — | Get JWT (form: `username`=email, `password`) |
| GET | `/api/auth/me` | user | Current user |
| GET | `/api/jobs` | — | Browse jobs (filter by company / search) |
| POST | `/api/cv` | user | Upload CV (PDF/TXT/text) → async profiling |
| POST | `/api/matches/run` | user | Kick off matching pipeline |
| GET | `/api/matches` | user | Scored matches (filter score/company/saved/applied) |
| PUT | `/api/matches/{job_id}/tracker` | user | Bookmark / mark applied |
| POST | `/api/admin/scrape` | admin | Trigger scrapers |
| POST | `/api/admin/embed` | admin | Embed new jobs |

## Data model

`users` → `user_profiles` (resume + work-profile + CV embedding) ·
`jobs` (deduped on `apply_url`, holds `raw` JSON + job embedding) ·
`job_scores` (per user × job) · `trackers` (per user saved/applied) · `scrape_logs`.

## Scrapers

The plugin architecture is preserved from the original project. `BaseScraper.scrape(repo)`
is the only contract; persistence goes through `JobRepository` (Postgres) instead of a JSON
dict. **Amazon** (API) and **Google** (Selenium) ship as reference implementations; the
other six companies (JPMorgan, Goldman, Mastercard, Visa, Microsoft, Citi) port mechanically
— see `src/jobradar/scrapers/__init__.py`.

Run locally:

```bash
jobradar-scrape --list
jobradar-scrape --companies amazon
```

## Testing

```bash
make test        # pytest (needs Postgres; DB tests auto-skip if unreachable)
make lint        # ruff
make typecheck   # mypy
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest against a pgvector Postgres service.

## Project structure

```
src/jobradar/
  main.py            FastAPI app factory
  config.py          env-driven settings (pydantic-settings)
  core/security.py   JWT + password hashing
  db/                engine, session, ORM models
  schemas/           pydantic request/response models
  api/routers/       auth, jobs, matches, cv, admin, health
  scrapers/          base, browser, helpers, repository, registry, amazon, google, cli
  services/          scoring, embeddings, cv_extract, matching
  workers/           celery app + tasks
migrations/          alembic (initial schema incl. pgvector + HNSW indexes)
frontend/index.html  minimal web client
tests/               pytest suite
```

## What changed from v1 → v2

- JSON files → **PostgreSQL + pgvector** (concurrent, queryable, indexed).
- Single hard-coded resume → **multi-user** accounts, each with their own CV and scores.
- Standalone scripts → **FastAPI service** + **Celery** background workers + **Beat** schedule.
- Secrets/paths in code → **env-based config**, `.env.example`, nothing sensitive committed.
- No tests/CI → **pytest + Ruff + GitHub Actions + pre-commit**, fully **Dockerized**.

## Deploy (make it a public website)

The dashboard is served by the API at `/`, so the whole product is one deployable unit.

**Free, resume-friendly (Render Free web + Neon Free Postgres, no Redis):**
1. Create a free Postgres project at neon.com and copy the pooled connection string.
2. In Neon's SQL editor, run `CREATE EXTENSION IF NOT EXISTS vector;`.
3. Push this repo to GitHub. On render.com, create a **Blueprint** from this repo, or create
   a **Web Service** manually. The checked-in `render.yaml` is intentionally free-only.
4. Set env vars:
   `DATABASE_URL` = the Neon pooled connection string;
   `ENVIRONMENT=production`;
   `RUN_TASKS_INLINE=true`;
   `JWT_SECRET_KEY` = a long random string;
   `ADMIN_EMAIL` + `ADMIN_PASSWORD` = your first admin login;
   `SEED_DEMO_DATA=true` for two harmless sample jobs;
   `PORTKEY_API_KEY` + `PORTKEY_VIRTUAL_KEY` or `OPENAI_API_KEY`;
   `CORS_ORIGINS=*` for first deploy, then replace it with your Render URL.
5. Deploy. The service runs `alembic upgrade head`, then starts FastAPI on Render's `$PORT`.
6. Open `/health` and `/health/db`, log in with `ADMIN_EMAIL`, then use Admin → scrape
   `amazon`, Admin → embed, upload a CV, and run matching.

Render's Free web service sleeps after idle time and wakes on the next request. Neon's free
database is a better fit here than Render's Free Postgres because Render's Free Postgres
expires after 30 days.

**Paid/full stack later:** run the same Docker image with Redis plus a Celery worker and beat:
`celery -A jobradar.workers.celery_app.celery_app worker` and
`celery -A jobradar.workers.celery_app.celery_app beat`.

**Any Docker host:** the app is a standard container. Run the API with
`alembic upgrade head && uvicorn jobradar.main:app --host 0.0.0.0 --port $PORT`, plus a
worker (`celery -A jobradar.workers.celery_app.celery_app worker`) and beat process,
pointed at a Postgres+pgvector database and a Redis instance.

**Notes & cheaper path:** managed hosts change plans/service types often — treat
`render.yaml` as a starting point. Background workers need a paid instance on most hosts;
for a low-cost demo you can run just the web service (free) against a free managed
Postgres+pgvector (e.g. Neon or Supabase) and free Redis (e.g. Upstash), and trigger
scraping/embedding/matching on demand instead of via always-on workers. For production
also: set `CORS_ORIGINS` to your domain, serve over HTTPS (hosts do this for you), rotate
`JWT_SECRET_KEY`, and move CV storage to object storage if you expect volume.

## Roadmap / next steps

- Port the remaining six scrapers into the registry.
- RAG "career coach" endpoint (`/api/chat`) over each user's scored jobs.
- Rate limiting, refresh tokens, email verification.
- Daily "new top matches" notification email.
- Resume tailoring endpoint (JD-aware rewrite).
- Managed deploy (Fly.io / Render / a DigitalOcean droplet) + object storage for CVs.

## License

MIT
