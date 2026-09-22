# Attendance Monitor

A personal, read-only foundation for monitoring attendance in the PW Institute of Innovation student portal. It never submits attendance, alters portal data, or automates credentials.

## Setup

1. Create and activate a Python 3.12+ virtual environment.
2. Install backend dependencies:

   ```bash
   pip install -r backend/requirements.txt
   playwright install chromium
   ```

3. Optionally copy `backend/.env.example` to `.env` and adjust non-sensitive settings.
4. Launch the browser connection:

   ```bash
   python run.py
   ```

The first visit may show the PWIOI login page. Log in manually in the Chromium window; the local `browser-profile/` directory preserves that session for later runs. Press `Ctrl+C` in the terminal to close the browser.

## API

Run the backend from `backend/`:

```bash
uvicorn app.main:app --reload
```

`GET /api/health` returns `{ "status": "ok" }`.

## Tests

From `backend/`:

```bash
pytest
```

