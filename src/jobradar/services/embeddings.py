"""Embeddings + pgvector recall.

Stage 1 of the two-stage retrieval design from the original roadmap: cheap semantic
recall over all jobs, so the expensive LLM scorer only sees the top candidates.
"""

import logging

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.config import settings
from jobradar.db.models import Job
from jobradar.services.free_fallback import ai_credentials_configured, local_embedding
from jobradar.services.llm import get_client

log = logging.getLogger(__name__)


def embed_text(text: str, client: OpenAI | None = None) -> list[float]:
    if client is None and not ai_credentials_configured():
        return local_embedding(text)

    client = client or get_client()
    resp = client.embeddings.create(model=settings.embedding_model, input=text[:8000])
    return resp.data[0].embedding


def embed_texts(texts: list[str], client: OpenAI | None = None) -> list[list[float]]:
    if client is None and not ai_credentials_configured():
        return [local_embedding(text) for text in texts]

    client = client or get_client()
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def recall_top_jobs(db: Session, embedding: list[float], limit: int) -> list[Job]:
    """Nearest jobs to `embedding` by cosine distance (pgvector). Falls back to the
    most recent jobs if nothing has been embedded yet."""
    stmt = (
        select(Job)
        .where(Job.embedding.is_not(None))
        .order_by(Job.embedding.cosine_distance(embedding))
        .limit(limit)
    )
    jobs = list(db.scalars(stmt))
    if jobs:
        return jobs
    log.warning("No embedded jobs found — falling back to most recent jobs")
    return list(db.scalars(select(Job).order_by(Job.fetched_date.desc()).limit(limit)))
