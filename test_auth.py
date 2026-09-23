import sqlite3
import uuid

from fastapi.testclient import TestClient

from app import app
from auth import OTP_MAX_ATTEMPTS, auth_service
from email_service import email_service


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex}@example.com"


def test_registration_login_otp_and_logout(monkeypatch):
    delivered = {}

    def capture_otp(recipient, otp, purpose):
        delivered.update(recipient=recipient, otp=otp, purpose=purpose)

    monkeypatch.setattr(email_service, "send_otp", capture_otp)
    client = TestClient(app)
    email = unique_email()
    password = "StrongDemoPassword!42"

    assert client.get("/dashboard", follow_redirects=False).status_code == 303
    assert client.get("/api/state").status_code == 401

    registration = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert registration.status_code == 202
    challenge_id = registration.json()["challenge_id"]
    assert "otp" not in registration.json()
    assert delivered["recipient"] == email
    assert delivered["purpose"] == "register"

    with sqlite3.connect(auth_service.database_path) as connection:
        stored_hash = connection.execute(
            "SELECT otp_hash FROM otp_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()[0]
    assert stored_hash != delivered["otp"]

    incorrect = client.post(
        "/api/auth/verify",
        json={"challenge_id": challenge_id, "otp": "000000"},
    )
    if delivered["otp"] == "000000":
        assert incorrect.status_code == 200
    else:
        assert incorrect.status_code == 400
        verified = client.post(
            "/api/auth/verify",
            json={"challenge_id": challenge_id, "otp": delivered["otp"]},
        )
        assert verified.status_code == 200

    assert client.get("/dashboard", follow_redirects=False).status_code == 200
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/auth/me").json()["email"] == email

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/state").status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 202
    assert delivered["purpose"] == "login"
    verified_login = client.post(
        "/api/auth/verify",
        json={"challenge_id": login.json()["challenge_id"], "otp": delivered["otp"]},
    )
    assert verified_login.status_code == 200
    assert client.get("/api/state").status_code == 200


def test_otp_is_locked_after_five_wrong_attempts(monkeypatch):
    delivered = {}
    monkeypatch.setattr(
        email_service,
        "send_otp",
        lambda recipient, otp, purpose: delivered.update(otp=otp),
    )
    client = TestClient(app)
    registration = client.post(
        "/api/auth/register",
        json={"email": unique_email(), "password": "AnotherStrongPassword!42"},
    )
    challenge_id = registration.json()["challenge_id"]
    wrong_otp = "111111" if delivered["otp"] != "111111" else "222222"

    for attempt in range(OTP_MAX_ATTEMPTS):
        response = client.post(
            "/api/auth/verify",
            json={"challenge_id": challenge_id, "otp": wrong_otp},
        )
        assert response.status_code == (429 if attempt == OTP_MAX_ATTEMPTS - 1 else 400)

    rejected = client.post(
        "/api/auth/verify",
        json={"challenge_id": challenge_id, "otp": delivered["otp"]},
    )
    assert rejected.status_code == 400
