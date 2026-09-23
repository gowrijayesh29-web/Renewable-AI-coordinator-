# Renewable AI Coordinator

This repository combines the team modules into one deployable FastAPI service:

- renewable generation, battery and grid simulator;
- explainable rule-based decision engine;
- workload management and decision application;
- live web dashboard;
- password login with OTP-based email verification.

## Run locally on Windows

Open PowerShell in this folder, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Before starting the server, replace these values in `.env`:

- `SMTP_USERNAME` and `SMTP_FROM_EMAIL`: the Gmail address used by the project;
- `SMTP_PASSWORD`: its 16-character Google App Password (not the normal password);
- `SESSION_SECRET` and `OTP_SECRET`: two different random secrets.

Generate each random secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `COOKIE_SECURE=false` locally. Never upload `.env`; it is already ignored by Git.

Open <http://127.0.0.1:8000/>. API documentation is available at
<http://127.0.0.1:8000/docs>.

## Authentication flow

1. Create an account with an email and a password of at least eight characters.
2. The password is stored as an Argon2 hash.
3. A six-digit OTP is emailed to the address and expires after five minutes.
4. The user has five verification attempts.
5. Successful verification creates an eight-hour, HttpOnly session cookie.
6. Every dashboard API requires that session; logout deletes it.

Returning users enter their email and password, receive a fresh OTP, and must
verify it before the dashboard opens.

## Test

```powershell
python -m pytest -v
```

## Integrated workflow

1. `Simulator` supplies energy, battery, forecast and workload state.
2. `decision_engine.generate_decision(state)` creates explainable decisions.
3. `WorkloadManager` applies those decisions to the same workload state.
4. The dashboard displays live state and recommendations.

Use `POST /api/coordinate` to generate and apply all current recommendations.
Individual decisions can be applied with `POST /api/decisions`.

## Main endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Redirect to login or dashboard |
| GET | `/login` | Registration and login page |
| GET | `/dashboard` | Protected dashboard |
| POST | `/api/auth/register` | Register and send verification OTP |
| POST | `/api/auth/login` | Verify password and send login OTP |
| POST | `/api/auth/verify` | Verify OTP and create session |
| GET | `/api/auth/me` | Current authenticated user |
| POST | `/api/auth/logout` | Clear session |
| GET | `/api/health` | Deployment health check |
| GET | `/api/state` | Complete current state |
| GET | `/api/decisions` | Generate recommendations |
| POST | `/api/coordinate` | Generate and apply recommendations |
| GET/POST | `/api/workloads` | List or add workloads |
| POST | `/api/decisions` | Apply one workload decision |
| GET | `/api/schedule` | Scheduled workloads |
| GET | `/api/status` | Workload counts |
| GET | `/api/history` | Applied-decision history |
| POST | `/api/reset` | Reset the demo |

## Deploy with Render

The included `Dockerfile` and `render.yaml` make the project deployment-ready.

1. Push this folder to a GitHub repository.
2. In Render, select **New > Blueprint**.
3. Connect the repository and select `render.yaml`.
4. Deploy and wait for `/api/health` to report `healthy`.

When Render asks for secret environment variables, enter:

- `SMTP_USERNAME`: project Gmail address;
- `SMTP_PASSWORD`: Google App Password;
- `SMTP_FROM_EMAIL`: the same project Gmail address.

The Blueprint generates `SESSION_SECRET` and `OTP_SECRET`, and enables secure
cookies automatically. The local SQLite account database is suitable for the
challenge demo; Render's temporary filesystem can reset it after a restart or
redeploy. Use a persistent database before treating this as a production user
system.

The host supplies `PORT`; no hardcoded production URL is required because the
dashboard uses relative API paths.
