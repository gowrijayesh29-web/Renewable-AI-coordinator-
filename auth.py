import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash

from email_service import EmailDeliveryError, email_service


OTP_TTL_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
SESSION_TTL_SECONDS = 8 * 60 * 60


class RegistrationRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class OTPVerificationRequest(BaseModel):
    challenge_id: str
    otp: str = Field(pattern=r"^\d{6}$")


class AuthService:
    def __init__(self) -> None:
        database_path = Path(os.getenv("AUTH_DB_PATH", "data/auth.db"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.password_hash = PasswordHash.recommended()
        self.otp_secret = os.getenv("OTP_SECRET") or secrets.token_urlsafe(32)
        self.session_secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
        self.cookie_name = "renewable_session"
        self.cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS otp_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL CHECK(purpose IN ('register', 'login')),
                    otp_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    used INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )
                """
            )

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    def _otp_digest(self, challenge_id: str, otp: str) -> str:
        value = f"{challenge_id}:{otp}".encode()
        return hmac.new(self.otp_secret.encode(), value, hashlib.sha256).hexdigest()

    def _create_challenge(self, email: str, purpose: str) -> tuple[str, str]:
        challenge_id = str(uuid.uuid4())
        otp = f"{secrets.randbelow(1_000_000):06d}"
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE otp_challenges SET used = 1
                WHERE email = ? AND purpose = ? AND used = 0
                """,
                (email, purpose),
            )
            connection.execute(
                """
                INSERT INTO otp_challenges
                    (challenge_id, email, purpose, otp_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge_id,
                    email,
                    purpose,
                    self._otp_digest(challenge_id, otp),
                    now + OTP_TTL_SECONDS,
                    now,
                ),
            )
        return challenge_id, otp

    def _invalidate_challenge(self, challenge_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE otp_challenges SET used = 1 WHERE challenge_id = ?",
                (challenge_id,),
            )

    def register(self, email: str, password: str) -> str:
        normalized = self.normalize_email(email)
        password_digest = self.password_hash.hash(password)
        now = int(time.time())

        with self._connect() as connection:
            user = connection.execute(
                "SELECT is_verified FROM users WHERE email = ?", (normalized,)
            ).fetchone()
            if user and user["is_verified"]:
                raise HTTPException(status_code=409, detail="An account already exists for this email.")
            if user:
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE email = ?",
                    (password_digest, normalized),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO users (email, password_hash, is_verified, created_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    (normalized, password_digest, now),
                )

        challenge_id, otp = self._create_challenge(normalized, "register")
        try:
            email_service.send_otp(normalized, otp, "register")
        except EmailDeliveryError as error:
            self._invalidate_challenge(challenge_id)
            raise HTTPException(status_code=503, detail=str(error)) from error
        return challenge_id

    def login(self, email: str, password: str) -> str:
        normalized = self.normalize_email(email)
        with self._connect() as connection:
            user = connection.execute(
                "SELECT password_hash, is_verified FROM users WHERE email = ?",
                (normalized,),
            ).fetchone()

        if not user or not self.password_hash.verify(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if not user["is_verified"]:
            raise HTTPException(
                status_code=403,
                detail="Complete email verification by registering again.",
            )

        challenge_id, otp = self._create_challenge(normalized, "login")
        try:
            email_service.send_otp(normalized, otp, "login")
        except EmailDeliveryError as error:
            self._invalidate_challenge(challenge_id)
            raise HTTPException(status_code=503, detail=str(error)) from error
        return challenge_id

    def verify_otp(self, challenge_id: str, otp: str) -> tuple[str, str]:
        now = int(time.time())
        with self._connect() as connection:
            challenge = connection.execute(
                "SELECT * FROM otp_challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()

            if not challenge or challenge["used"]:
                raise HTTPException(status_code=400, detail="This verification request is invalid or used.")
            if challenge["expires_at"] < now:
                connection.execute(
                    "UPDATE otp_challenges SET used = 1 WHERE challenge_id = ?",
                    (challenge_id,),
                )
                connection.commit()
                raise HTTPException(status_code=400, detail="The OTP has expired. Request a new one.")
            if challenge["attempts"] >= OTP_MAX_ATTEMPTS:
                connection.execute(
                    "UPDATE otp_challenges SET used = 1 WHERE challenge_id = ?",
                    (challenge_id,),
                )
                connection.commit()
                raise HTTPException(status_code=429, detail="Too many incorrect attempts.")

            expected = challenge["otp_hash"]
            received = self._otp_digest(challenge_id, otp)
            if not hmac.compare_digest(expected, received):
                attempts = challenge["attempts"] + 1
                connection.execute(
                    """
                    UPDATE otp_challenges
                    SET attempts = ?, used = CASE WHEN ? >= ? THEN 1 ELSE used END
                    WHERE challenge_id = ?
                    """,
                    (attempts, attempts, OTP_MAX_ATTEMPTS, challenge_id),
                )
                connection.commit()
                remaining = OTP_MAX_ATTEMPTS - attempts
                if remaining <= 0:
                    raise HTTPException(status_code=429, detail="Too many incorrect attempts.")
                raise HTTPException(
                    status_code=400,
                    detail=f"Incorrect OTP. {remaining} attempt(s) remaining.",
                )

            connection.execute(
                "UPDATE otp_challenges SET used = 1 WHERE challenge_id = ?",
                (challenge_id,),
            )
            if challenge["purpose"] == "register":
                connection.execute(
                    "UPDATE users SET is_verified = 1 WHERE email = ?",
                    (challenge["email"],),
                )

        return challenge["email"], challenge["purpose"]

    def create_session_token(self, email: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {"sub": email, "iat": now, "exp": now + SESSION_TTL_SECONDS},
            self.session_secret,
            algorithm="HS256",
        )

    def session_email(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            payload = jwt.decode(token, self.session_secret, algorithms=["HS256"])
            email = payload.get("sub")
            return email if isinstance(email, str) and email else None
        except jwt.PyJWTError:
            return None

    def set_session_cookie(self, response: Response, email: str) -> None:
        response.set_cookie(
            key=self.cookie_name,
            value=self.create_session_token(email),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=self.cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(
            key=self.cookie_name,
            httponly=True,
            secure=self.cookie_secure,
            samesite="lax",
            path="/",
        )


auth_service = AuthService()
router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegistrationRequest):
    challenge_id = auth_service.register(str(payload.email), payload.password)
    return {
        "message": "A verification code was sent to your email.",
        "challenge_id": challenge_id,
        "expires_in_seconds": OTP_TTL_SECONDS,
    }


@router.post("/login", status_code=status.HTTP_202_ACCEPTED)
def login(payload: LoginRequest):
    challenge_id = auth_service.login(str(payload.email), payload.password)
    return {
        "message": "A verification code was sent to your email.",
        "challenge_id": challenge_id,
        "expires_in_seconds": OTP_TTL_SECONDS,
    }


@router.post("/verify")
def verify(payload: OTPVerificationRequest, response: Response):
    email, purpose = auth_service.verify_otp(payload.challenge_id, payload.otp)
    auth_service.set_session_cookie(response, email)
    return {"message": "Verification successful.", "email": email, "purpose": purpose}


@router.get("/me")
def current_user(request: Request):
    email = auth_service.session_email(request.cookies.get(auth_service.cookie_name))
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return {"email": email}


@router.post("/logout")
def logout(response: Response):
    auth_service.clear_session_cookie(response)
    return {"message": "Logged out successfully."}
