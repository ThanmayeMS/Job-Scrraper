"""LLM job fit-scoring (OpenAI). Per-user: takes a resume + a job's raw payload."""

import json
import logging

from openai import OpenAI

from jobradar.config import settings
from jobradar.services.free_fallback import ai_credentials_configured, score_by_keyword_overlap
from jobradar.services.llm import get_client

log = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a recruiter evaluating job fit. Given a candidate resume and \
a job description, return ONLY a valid JSON object. No markdown, no explanation.

JSON schema (return exactly this structure):
{{
  "score": <integer 1-10>,
  "reason": "<2 sentences explaining the score>",
  "matching_skills": ["<skill1>", "<skill2>", "<skill3>"],
  "gaps": "<1 sentence on what the candidate is missing>",
  "info_level": "<Sufficient, Partial, or Insufficient>"
}}

Scoring guide: 9-10 near-perfect, 7-8 strong, 5-6 moderate, 3-4 weak, 1-2 poor.

---
CANDIDATE RESUME:
{resume}

---
JOB DESCRIPTION:
{job_text}
"""

# Fields, in priority order, pulled from a job's raw company-specific payload.
TEXT_FIELDS = (
    "title",
    "organization",
    "about_the_job",
    "responsibilities",
    "minimum_qualifications",
    "preferred_qualifications",
    "description",
    "summary",
    "job_description",
    "qualifications",
    "required_qualifications",
    "basic_qualifications",
    "our_impact",
    "your_impact",
    "overview",
    "role",
    "all_about_you",
    "job_function",
    "job_family",
    "experience_level",
    "department",
)


def extract_job_text(raw: dict, max_chars: int = 4000) -> str:
    parts = []
    for field in TEXT_FIELDS:
        val = raw.get(field)
        if isinstance(val, str) and val.strip():
            parts.append(f"[{field.upper()}]\n{val.strip()}")
        elif isinstance(val, list):
            joined = " ".join(str(v) for v in val if v)
            if joined.strip():
                parts.append(f"[{field.upper()}]\n{joined.strip()}")
    return "\n\n".join(parts)[:max_chars]


def score_job(resume_text: str, raw: dict, client: OpenAI | None = None) -> dict:
    job_text = extract_job_text(raw)
    if not job_text.strip():
        return {
            "score": 0,
            "reason": "No job description available.",
            "matching_skills": [],
            "gaps": "N/A",
            "info_level": "Insufficient",
        }

    if client is None and not ai_credentials_configured():
        return score_by_keyword_overlap(resume_text, job_text)

    client = client or get_client()
    prompt = PROMPT_TEMPLATE.format(resume=resume_text.strip(), job_text=job_text)
    response = client.chat.completions.create(
        model=settings.scoring_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)
