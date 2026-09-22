# PWIOI Attendance Monitor

A personal, read-only automation system for monitoring attendance on the PW Institute of Innovation (PWIOI) portal. It automatically checks attendance at a configurable time (default 4:30 PM IST) and sends a report via Gmail.

**Key principles:**
- **Read-only**: Never submits, modifies, or automates attendance on PWIOI
- **No hard-coded data**: Subjects, periods, and attendance statuses come dynamically from PWIOI
- **Source of truth**: PWIOI portal is the only source for what classes were scheduled and their status
- **Privacy-first**: No credentials stored; uses your authenticated browser session and OAuth for Gmail

## Features

- 🕐 **Automated daily checks** at configurable time (default 4:30 PM Asia/Kolkata)
- 📧 **Gmail reports** with present/absent/not-marked breakdown
- 🌐 **Web dashboard** for viewing history, manual checks, and schedule configuration
- 🔐 **Secure authentication**: Manual PWIOI login + Google OAuth for Gmail (no passwords stored)
- 📊 **History tracking** with SQLite persistence
- ⚙️ **Runtime configuration** of check time and timezone via dashboard

## Architecture

```
┌─────────────┐     Playwright      ┌──────────────┐
│  Scheduler  │ ──────────────────▶ │  PWIOI Portal │
│  (APScheduler)│  (persistent      │  (read-only)  │
└──────┬──────┘   browser session)  └──────┬───────┘
       │                                    │
       ▼                                    ▼
┌──────────────┐                    ┌──────────────┐
│   Parser     │                    │  SQLite DB   │
│ (attendance) │                    │  (history)   │
└──────┬───────┘                    └──────────────┘
       │
       ▼
┌──────────────┐
│ Gmail API    │
│ (OAuth)      │
└──────────────┘
```

## Quick Start

### Prerequisites
- Python 3.12+
- Google Cloud Console project with Gmail API enabled
- OAuth 2.0 Client ID (Desktop app type) for Gmail

### Installation

```bash
# 1. Clone and enter project
cd attendance-monitor

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r backend/requirements.txt
playwright install chromium

# 4. Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env with your settings (see Configuration below)

# 5. Set up Gmail OAuth
# Place your Google OAuth client_secret.json in the project root
# (or update GOOGLE_CLIENT_SECRET_FILE in .env)

# 6. First run - authenticate with PWIOI
python run.py
# → Chromium opens → Log into PWIOI manually → Press Enter in terminal
# → Press Ctrl+C to close browser

# 7. Authorize Gmail (one-time)
python run.py --authorize-gmail
# → Browser opens → Grant Gmail send permission → Token saved locally

# 8. Test full check workflow
python run.py --run-check

# 9. Start the web server
uvicorn app.main:app --reload --port 8000 --app-dir backend
# Open http://localhost:8000
```

### Running as a Service

For production, run the API server which includes the scheduler:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
```

The scheduler runs automatically in the background at the configured time.

## Configuration

All settings in `backend/.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PWIOI_ATTENDANCE_URL` | `https://app.pwioi.club/dashboard/student/attendance` | PWIOI attendance page URL |
| `BROWSER_PROFILE_DIR` | `browser-profile` | Persistent Chromium profile directory |
| `DATABASE_URL` | `sqlite:///./data/attendance.db` | SQLite database path |
| `CHECK_TIME` | `16:30` | Daily check time (24h HH:MM) |
| `TIMEZONE` | `Asia/Kolkata` | Timezone for scheduled checks |
| `GOOGLE_CLIENT_SECRET_FILE` | `client_secret.json` | Google OAuth client secret path |
| `GMAIL_TOKEN_FILE` | `data/gmail_token.json` | Gmail OAuth token (auto-generated) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `HEADLESS` | `false` | Run browser headless (for servers) |

**Runtime overrides**: Check time and timezone can be changed via the web dashboard (Settings card). These are stored in the database and persist across restarts.

## CLI Commands

```bash
# Open browser for manual PWIOI login/session maintenance
python run.py

# Inspect PWIOI attendance page DOM (for selector debugging)
python run.py --inspect

# Parse today's attendance only (read-only, no storage/email)
python run.py --check-now

# Run full check workflow (parse + store + email)
python run.py --run-check

# Authorize Gmail API access
python run.py --authorize-gmail
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/status` | Scheduler status, next run, current schedule |
| GET | `/api/schedule` | Get current schedule settings |
| PUT | `/api/schedule` | Update schedule (check_time, timezone) |
| GET | `/api/latest` | Latest attendance check result |
| GET | `/api/history` | Paginated check history |
| GET | `/api/history/{id}` | Detailed check result |
| POST | `/api/check-now` | Trigger manual attendance check |
| GET | `/api/gmail-status` | Gmail authorization status |
| GET | `/` | Web dashboard |

### Schedule Settings API

```bash
# Get current schedule
curl http://localhost:8000/api/schedule

# Update schedule (24h format, IANA timezone)
curl -X PUT http://localhost:8000/api/schedule \
  -H "Content-Type: application/json" \
  -d '{"check_time": "17:00", "timezone": "America/New_York"}'
```

## Web Dashboard

The dashboard at `/` provides:

- **Schedule Settings** — Configure check time and timezone (persists to database)
- **Today's Attendance** — Latest check results grouped by period
- **Summary Stats** — Present/Absent/Not Marked counts
- **Gmail Status** — Authorization state and recipient email
- **History Table** — Past checks with email status and detail view

## How It Works

### 1. Authentication Flow
- **PWIOI**: Uses your existing browser session. On first run, Chromium opens PWIOI login → you sign in with Google manually → session saved to `browser-profile/`
- **Gmail**: OAuth 2.0 flow (`run.py --authorize-gmail`) → token saved to `data/gmail_token.json`

### 2. Daily Check Process (at scheduled time)
1. Scheduler triggers `AttendanceWorkflow.run_check()`
2. Playwright opens authenticated browser to PWIOI attendance page
3. Parser extracts today's attendance:
   - Reads all subjects from portal
   - Finds records matching today's date
   - Normalizes status: PRESENT / ABSENT / NOT_MARKED / UNKNOWN
   - **Never** treats missing/parse failure as ABSENT
4. Stores `CheckResult` + `SubjectAttendanceRecord` in SQLite
5. If Gmail authorized, sends formatted report via Gmail API
6. Updates check record with email status

### 3. Dynamic Subject Detection
No hard-coded subjects. The parser:
- Fetches subject names from PWIOI DOM
- Checks each subject's Daily Records for today's date
- If record exists → class was scheduled → report status
- If no record → class not scheduled → ignore

## Project Structure

```
attendance-monitor/
├── backend/
│   ├── app/
│   │   ├── attendance.py      # Read-only PWIOI parser
│   │   ├── browser.py         # Playwright persistent context
│   │   ├── config.py          # Settings + runtime overrides
│   │   ├── database.py        # SQLAlchemy setup
│   │   ├── main.py            # FastAPI app + endpoints
│   │   ├── models.py          # Pydantic + SQLAlchemy models
│   │   ├── notifier.py        # Gmail API + OAuth
│   │   ├── portal.py          # PWIOI navigation helpers
│   │   ├── portal_inspector.py# DOM inspection tool
│   │   ├── scheduler.py       # APScheduler integration
│   │   └── workflow.py        # Check orchestration
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── templates/dashboard.html
│   └── static/
│       ├── css/dashboard.css
│       └── js/dashboard.js
├── browser-profile/           # Persistent Chromium session (gitignored)
├── data/                      # SQLite DB + Gmail token (gitignored)
├── artifacts/                 # DOM inspection outputs
├── run.py                     # CLI entry point
└── README.md
```

## Development

### Running Tests
```bash
cd backend
pytest -v
```

### DOM Inspection (when PWIOI UI changes)
```bash
python run.py --inspect
# → Artifacts saved to artifacts/
# → Review artifacts/DOM_NOTES.md
# → Update selectors in attendance.py PAGE_SPECIFIC_SELECTORS
```

### Adding New Selectors
After `run.py --inspect`, update `attendance.py`:
```python
PAGE_SPECIFIC_SELECTORS = (
    "[data-testid='attendance-date']",
    "table.attendance-table tbody tr",
    # ... confirmed selectors from DOM_NOTES.md
)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Not authenticated" | Run `python run.py`, log into PWIOI manually |
| "Gmail not authorized" | Run `python run.py --authorize-gmail` |
| Parse fails / empty results | Run `python run.py --inspect`, update selectors |
| Scheduler not running | Check `/api/status`, ensure server is running |
| Timezone issues | Verify TIMEZONE in .env or use dashboard Settings |
| Browser crashes | Delete `browser-profile/`, re-authenticate |

## Security Notes

- **No passwords stored** — PWIOI uses your browser session; Gmail uses OAuth tokens
- **Local-only tokens** — `browser-profile/`, `data/gmail_token.json`, `.env` are gitignored
- **Read-only** — Never writes to PWIOI, only reads attendance
- **Minimal scopes** — Gmail OAuth only requests `gmail.send`

## License

Personal project for educational use. Not affiliated with PWIOI.