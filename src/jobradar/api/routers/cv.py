from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from jobradar.api.deps import CurrentUser, DbSession
from jobradar.db.models import UserProfile
from jobradar.services.cv_extract import extract_pdf_text

router = APIRouter(prefix="/api/cv", tags=["cv"])


class CvStatus(BaseModel):
    has_cv: bool
    has_work_profile: bool
    has_embedding: bool
    resume_chars: int = 0


def _profile_status(profile: UserProfile | None) -> CvStatus:
    if profile is None:
        return CvStatus(has_cv=False, has_work_profile=False, has_embedding=False)
    return CvStatus(
        has_cv=bool(profile.resume_text),
        has_work_profile=bool(profile.work_profile),
        has_embedding=profile.embedding is not None,
        resume_chars=len(profile.resume_text or ""),
    )


@router.get("", response_model=CvStatus)
def get_cv(db: DbSession, user: CurrentUser):
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return _profile_status(profile)


@router.post("", response_model=CvStatus, status_code=201)
async def upload_cv(
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
):
    """Upload a CV as a PDF/TXT file or as raw text. Triggers async profiling."""
    resume_text = ""
    if file is not None:
        data = await file.read()
        if file.filename and file.filename.lower().endswith(".pdf"):
            resume_text = extract_pdf_text(data)
        else:
            resume_text = data.decode("utf-8", errors="ignore")
    elif text:
        resume_text = text

    if not resume_text.strip():
        raise HTTPException(status_code=400, detail="Provide a non-empty CV file or text")

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
    profile.resume_text = resume_text
    profile.work_profile = None  # invalidate; will be rebuilt asynchronously
    profile.embedding = None
    db.commit()
    db.refresh(profile)

    # Fire-and-forget: extract work profile + embed. Safe to skip if broker is down.
    try:
        from jobradar.workers.tasks import build_user_profile_task

        build_user_profile_task.delay(user.id)
    except Exception:
        pass

    return _profile_status(profile)
