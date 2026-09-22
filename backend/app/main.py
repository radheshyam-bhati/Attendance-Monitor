"""Minimal FastAPI application."""

from fastapi import FastAPI

app = FastAPI(title="Attendance Monitor")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Report that the backend is available."""
    return {"status": "ok"}

