"""No-key fallback matching helpers.

These keep the live demo useful when OpenAI/Portkey credentials are absent. The
fallback is intentionally simple and transparent: keyword extraction, hashed local
vectors, and overlap-based scoring.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from jobradar.config import settings

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "you",
    "your",
}

DOMAIN_KEYWORDS = {
    "Data Engineering": {"data", "etl", "pipeline", "warehouse", "sql", "spark", "dbt"},
    "Business Analytics & BI": {
        "analytics",
        "dashboard",
        "reporting",
        "kpi",
        "tableau",
        "powerbi",
        "analysis",
    },
    "Data Science & ML": {"machine", "learning", "model", "python", "ml", "ai", "statistics"},
    "Software Engineering": {"api", "backend", "frontend", "service", "java", "react", "python"},
    "Product Management": {"product", "roadmap", "stakeholder", "launch", "strategy", "user"},
}


def ai_credentials_configured() -> bool:
    return bool(settings.portkey_api_key or settings.openai_api_key)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def top_terms(text: str, limit: int = 12) -> list[str]:
    return [term for term, _ in Counter(tokenize(text)).most_common(limit)]


def local_embedding(text: str, dim: int | None = None) -> list[float]:
    """Return a deterministic normalized bag-of-words vector."""
    size = dim or settings.embedding_dim
    vector = [0.0] * size
    for token, count in Counter(tokenize(text)).items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[idx] += sign * (1.0 + math.log(count))

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def build_local_work_profile(resume_text: str) -> str:
    terms = top_terms(resume_text, 16)
    term_set = set(terms)
    domain_scores = {
        domain: len(term_set & keywords) for domain, keywords in DOMAIN_KEYWORDS.items()
    }
    domain = max(domain_scores, key=domain_scores.get) if any(domain_scores.values()) else "Other"

    lower = resume_text.lower()
    seniority = (
        "Senior" if re.search(r"\b(senior|lead|manager|[5-9]\+?\s+years)\b", lower) else "Mid"
    )
    outputs = ", ".join(terms[:4]) if terms else "role-relevant deliverables"
    work_terms = ", ".join(terms[:8]) if terms else "the resume content"
    return (
        f"Seniority: {seniority}\n"
        f"Domain: {domain}\n"
        f"Output: {outputs}\n"
        f"Work: Keyword fallback profile built from the uploaded CV. "
        f"The strongest detected signals are: {work_terms}."
    )


def score_by_keyword_overlap(resume_text: str, job_text: str) -> dict:
    resume_terms = set(top_terms(resume_text, 40))
    job_terms = set(top_terms(job_text, 40))
    overlap = sorted(resume_terms & job_terms)
    if not job_text.strip():
        return {
            "score": 0,
            "reason": "No job description available.",
            "matching_skills": [],
            "gaps": "N/A",
            "info_level": "Insufficient",
        }

    ratio = len(overlap) / max(6, min(len(job_terms), 24))
    score = max(1, min(10, round(3 + ratio * 7)))
    missing = sorted(job_terms - resume_terms)[:5]
    matched = overlap[:6]
    reason = (
        "Keyword fallback score because no AI key is configured. "
        f"Matched resume/job signals: {', '.join(matched) if matched else 'limited overlap'}."
    )
    return {
        "score": score,
        "reason": reason,
        "matching_skills": matched,
        "gaps": ", ".join(missing) if missing else "No major keyword gaps detected.",
        "info_level": "Partial" if matched else "Insufficient",
    }
