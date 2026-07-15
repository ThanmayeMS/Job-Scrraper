"""End-to-end API tests covering the full user journey.

Hermetic: no external network calls (LLM creds are cleared in conftest, so inline
profiling/scoring no-ops). Exercises auth, CV upload, job browsing, tracker, matches,
and admin authorization.
"""

import uuid

from jobradar.db.base import SessionLocal
from jobradar.db.models import Job


def _guest_headers(client) -> dict:
    r = client.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_job(company: str = "Amazon", title: str = "Data Engineer") -> str:
    url = f"https://example.com/jobs/{uuid.uuid4().hex}"
    db = SessionLocal()
    try:
        db.add(
            Job(
                apply_url=url,
                company=company,
                title=title,
                locations="Bengaluru, India",
                description="Build ETL pipelines and analytics dashboards.",
                raw={"title": title, "about_the_job": "Build ETL pipelines."},
            )
        )
        db.commit()
    finally:
        db.close()
    return url


def test_guest_login_and_me(client):
    headers = _guest_headers(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["email"].startswith("guest-")
    assert body["is_superuser"] is False


def test_register_then_login(client):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert response.status_code == 201
    r = client.post("/api/auth/login", data={"username": email, "password": "password123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == email


def test_cv_upload_file(client):
    headers = _guest_headers(client)
    files = {
        "file": (
            "cv.txt",
            b"Senior data analyst. SQL, Python, Tableau, ETL, dashboards.",
            "text/plain",
        )
    }
    r = client.post("/api/cv", headers=headers, files=files)
    assert r.status_code == 201, r.text
    assert r.json()["has_cv"] is True
    assert client.get("/api/cv", headers=headers).json()["has_cv"] is True


def test_jobs_browse_and_detail(client):
    url = _seed_job()
    headers = _guest_headers(client)
    listing = client.get("/api/jobs?limit=100", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1
    job = next(j for j in listing.json()["items"] if j["apply_url"] == url)
    detail = client.get(f"/api/jobs/{job['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["company"] == "Amazon"
    assert "Amazon" in client.get("/api/jobs/companies", headers=headers).json()


def test_tracker_update(client):
    url = _seed_job()
    headers = _guest_headers(client)
    job = next(
        j
        for j in client.get("/api/jobs?limit=100", headers=headers).json()["items"]
        if j["apply_url"] == url
    )
    r = client.put(f"/api/matches/{job['id']}/tracker", headers=headers, json={"saved": True})
    assert r.status_code == 200


def test_matches_empty_and_run(client):
    headers = _guest_headers(client)
    assert client.get("/api/matches", headers=headers).json()["total"] == 0
    # Enqueue/inline-run should never 500, even with no LLM creds.
    assert client.post("/api/matches/run", headers=headers).status_code == 202


def test_admin_requires_superuser(client):
    headers = _guest_headers(client)
    assert client.get("/api/admin/scrapers", headers=headers).status_code == 403


def test_unauthenticated_is_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/matches").status_code == 401
