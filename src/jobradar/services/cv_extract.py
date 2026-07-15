"""CV text extraction (PDF) and structured work-profile generation.

Mirrors the original extract_work_profiles.py idea: distil a resume into an
activity-focused profile so it embeds into the same semantic space as jobs.
"""

import io
import logging

import pdfplumber
from openai import OpenAI

from jobradar.config import settings
from jobradar.services.free_fallback import ai_credentials_configured, build_local_work_profile
from jobradar.services.llm import get_client

log = logging.getLogger(__name__)

WORK_PROFILE_PROMPT = """\
Given the CV below, write a structured summary in EXACTLY this format:

Seniority: <Entry | Mid | Senior | Lead | Manager | Director | Executive>
Domain: <e.g. Business Analytics & BI | Data Engineering | Data Science & ML | \
Software Engineering | Product Management | Financial Analysis | Other>
Output: <comma-separated list of 2-4 concrete artefacts this person produces>
Work: <3-5 sentences describing what this person does day-to-day>

Rules: start with what the person does; no tool/company names; no metrics or awards.

CV:
{resume_text}

Return ONLY the four lines above.
"""


def extract_pdf_text(data: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception as exc:
        log.warning("PDF extraction failed: %s", exc)
        return ""


def build_work_profile(resume_text: str, client: OpenAI | None = None) -> str:
    if client is None and not ai_credentials_configured():
        return build_local_work_profile(resume_text)

    client = client or get_client()
    resp = client.chat.completions.create(
        model=settings.scoring_model,
        messages=[
            {
                "role": "user",
                "content": WORK_PROFILE_PROMPT.format(resume_text=resume_text.strip()[:4000]),
            }
        ],
        temperature=0.2,
        max_tokens=250,
    )
    return resp.choices[0].message.content.strip()
