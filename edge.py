"""Edge uploader: the machine's side of the network link.

Every event goes into a local SQLite outbox first. Batches of unsent rows are
POSTed to the API and marked sent only when the server acks them.

Two failure modes are simulated:
  * an outage on day 3: every POST fails, the outbox grows, then the backlog
    is replayed (in shuffled batch order) once the link comes back;
  * lost acks: the server stores a batch but the response never arrives, so
    the edge sends the same events again later.

Usage: python edge.py [--api http://127.0.0.1:8742] [--events data/events.jsonl]
"""
import argparse
import json
import random
import sqlite3
from pathlib import Path

import httpx

BATCH_SIZE = 200
FLUSH_EVERY = 100                       # try to upload after every N new events
OUTAGE = ("2026-06-03T09:00", "2026-06-03T14:00")
LOST_ACK_P = 0.08


class LinkDown(Exception):
    pass


class Outbox:
    def __init__(self, path):
        Path(path).unlink(missing_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)"
        )

    def add(self, ev):
        with self.conn:
            self.conn.execute("INSERT INTO outbox (event_id, body) VALUES (?, ?)", (ev["event_id"], json.dumps(ev)))

    def unsent(self):
        return self.conn.execute("SELECT seq, body FROM outbox WHERE sent = 0 ORDER BY seq").fetchall()

    def mark_sent(self, seqs):
        with self.conn:
            self.conn.executemany("UPDATE outbox SET sent = 1 WHERE seq = ?", [(s,) for s in seqs])


class Uploader:
    def __init__(self, api, outbox, seed):
        self.client = httpx.Client(base_url=api, timeout=10)
        self.outbox = outbox
        self.rng = random.Random(seed)
        self.stats = {"posts": 0, "failed_posts": 0, "lost_acks": 0, "events_sent": 0, "max_backlog": 0}

    def post(self, batch, now):
        if OUTAGE[0] <= now < OUTAGE[1]:
            raise LinkDown()
        r = self.client.post("/events", json=batch)
        r.raise_for_status()
        if self.rng.random() < LOST_ACK_P:
            raise LinkDown()            # server has it, but we never hear back

    def flush(self, now):
        rows = self.outbox.unsent()
        self.stats["max_backlog"] = max(self.stats["max_backlog"], len(rows))
        batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
        if len(batches) > 1:
            self.rng.shuffle(batches)   # a backlog replay does not arrive in order
        for b in batches:
            self.stats["posts"] += 1
            try:
                self.post([json.loads(body) for _, body in b], now)
            except LinkDown:
                if OUTAGE[0] <= now < OUTAGE[1]:
                    self.stats["failed_posts"] += 1
                    return              # link is down, keep buffering
                self.stats["lost_acks"] += 1
                continue                # these rows stay unsent and go again next flush
            self.outbox.mark_sent([seq for seq, _ in b])
            self.stats["events_sent"] += len(b)


def run(api, events_path, outbox_path, seed=7):
    outbox = Outbox(outbox_path)
    up = Uploader(api, outbox, seed)
    now = ""
    with open(events_path) as f:
        for n, line in enumerate(f, 1):
            ev = json.loads(line)
            outbox.add(ev)
            now = max(now, ev["ts"])
            if n % FLUSH_EVERY == 0:
                up.flush(now)
    for _ in range(50):                 # end of run: drain whatever is left
        if not outbox.unsent():
            break
        up.flush(now)
    up.stats["left_in_outbox"] = len(outbox.unsent())
    return up.stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8742")
    p.add_argument("--events", default="data/events.jsonl")
    p.add_argument("--outbox", default="data/outbox.db")
    args = p.parse_args()
    stats = run(args.api, args.events, args.outbox)
    print(f"edge: {stats}")


if __name__ == "__main__":
    main()
