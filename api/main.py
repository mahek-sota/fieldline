from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import db
from api.report import build_report

app = FastAPI(title="fieldline")


class Event(BaseModel):
    event_id: str
    ts: str
    station: str
    type: str
    unit_id: Optional[int] = None
    cycle_s: Optional[float] = None
    reason: Optional[str] = None
    fault_id: Optional[str] = None
    duration_s: Optional[float] = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/events")
def post_events(events: List[Event]):
    conn = db.connect()
    try:
        accepted, duplicates = db.insert_events(conn, [e.model_dump(exclude_none=True) for e in events])
    finally:
        conn.close()
    return {"accepted": accepted, "duplicates": duplicates}


@app.get("/report")
def get_report():
    conn = db.connect()
    try:
        return build_report(db.all_events(conn))
    finally:
        conn.close()


web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.exists():
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
