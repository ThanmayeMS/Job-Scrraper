import uuid


def _creds():
    return {"email": f"user-{uuid.uuid4().hex[:8]}@example.com", "password": "password123"}


def test_register_login_me(client):
    creds = _creds()

    r = client.post("/api/auth/register", json=creds)
    assert r.status_code == 201
    assert r.json()["email"] == creds["email"]

    # Duplicate registration is rejected.
    assert client.post("/api/auth/register", json=creds).status_code == 409

    r = client.post(
        "/api/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == creds["email"]


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_login_wrong_password(client):
    creds = _creds()
    client.post("/api/auth/register", json=creds)
    r = client.post(
        "/api/auth/login",
        data={"username": creds["email"], "password": "wrong-password"},
    )
    assert r.status_code == 401
