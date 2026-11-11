"""Event storage. Plain SQL that runs unchanged on SQLite or Postgres."""
import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    station     TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL
);
"""


def db_path():
    return os.environ.get("FIELDLINE_DB", "data/server.db")


def connect(path=None):
    conn = sqlite3.connect(path or db_path())
    conn.execute(SCHEMA)
    return conn


def insert_events(conn, events):
    """Store events keyed by event_id. A repeat of a known id is ignored.

    Returns (accepted, duplicates).
    """
    now = datetime.now(timezone.utc).isoformat()
    accepted = 0
    with conn:
        for ev in events:
            cur = conn.execute(
                "INSERT INTO events (event_id, ts, station, type, payload, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING",
                (ev["event_id"], ev["ts"], ev["station"], ev["type"], json.dumps(ev), now),
            )
            accepted += cur.rowcount
    return accepted, len(events) - accepted


def all_events(conn):
    rows = conn.execute("SELECT payload FROM events ORDER BY ts, event_id").fetchall()
    return [json.loads(r[0]) for r in rows]
