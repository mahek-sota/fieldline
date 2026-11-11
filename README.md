# Fieldline

Where does a field machine lose time?

Fieldline simulates a small four station assembly line working on a construction site for five days, ships its event log over an unreliable network to an API, and turns the events into a report: units installed per day, hours lost to faults and to late material, the bottleneck station, and an early warning when a station starts slowing down before it breaks.

Everything is simulated. None of it is real machine data.

## What it does

- `sim.py` runs a SimPy model of four stations in a row (stage parts, assemble, quality check, install) with buffers of three units between them. Cycle times are random, stations fault at random with a reason code, parts deliveries are sometimes late, and the assemble station slowly gets about 30% slower over two shifts before it fails with `BEARING_WEAR`. The run is seeded, so the same seed gives the same events.
- `edge.py` plays the machine's side of the link. Every event goes into a local SQLite outbox first, and batches are POSTed to the API. On day 3 the network is down for five hours, so the outbox fills up and is replayed when the link returns, in shuffled order. Some responses are "lost" after the server has stored the batch, so those events are sent again.
- `api/` is a FastAPI app on SQLite. `POST /events` stores events keyed by their UUID and ignores any id it has already seen. `GET /report` builds the report from whatever is stored.
- `web/index.html` shows the report. It reads `/report` when served by the API and falls back to `web/report.json`, so the `web/` folder can be deployed as a static site on Vercel as is.

## How to run

Needs Python 3.9 or newer.

```
make demo     # simulate, upload through the flaky link, print the report, save web/report.json
make serve    # run the API and the page at http://127.0.0.1:8742/
make static   # build dist/index.html: one self-contained page with the report embedded
make test     # the three tests
```

If port 8742 is taken, use `make demo PORT=9000` (same for `serve`).

To share the page, run `make static` and deploy `dist/index.html` anywhere that serves static files (for example `cd dist && npx vercel`), or send the file itself. It needs no API and no other files; it only loads the chart library from a CDN.

## Design decisions

**Idempotent ingest instead of trying to send each event exactly once.** The machine cannot know whether a batch arrived when the response never comes back, so it has two choices: drop the batch and maybe lose data, or send it again and maybe duplicate it. Losing data is worse, so the edge always resends, and the server makes resending harmless by keeping each event's UUID as the primary key and ignoring repeats. The report is then computed from the set of stored events sorted by timestamp, so arrival order does not matter either. This is much simpler than any exactly-once protocol, and the test checks it: sending every event twice in random order gives the same report.

**Why CUSUM.** A slowly drifting station never produces one obviously bad cycle; every single cycle looks normal. CUSUM adds up small, consistent excesses over a baseline and alarms when the running total gets too big, which is exactly the shape of a bearing wearing out. It is a few lines of code and each parameter can be explained. Here it runs on the mean of each 30 consecutive cycles, using day 1 as the baseline, with a slack of 1 standard error and an alarm at 5. The slack had to be 1 rather than the textbook 0.5, because the day 1 baseline itself is off by up to half a standard error, and with 0.5 the healthy stations raised false alarms. After an alarm, each repair restarts the CUSUM; if cycle times stay normal for six batches, that repair is credited as the fix. That is how the report tells the bearing repair apart from unrelated jams and sensor faults that happened while the station was slowing down. With seed 7 the alarm came 16 operating hours (44 hours on the clock, including two nights) before the station failed. Across 12 seeds it alarmed on the drifting station every time, never on a healthy one, and credited the right repair in 11. In the other one a random fault came straight after the bearing repair, and the data cannot tell those two apart.

**What the simulation leaves out.** There is one shift a day and nights simply do not exist; the line stops and resumes. Stations are independent, with no shared power, no crew moving between stations, and no weather. Faults happen only between cycles and are repaired by a fixed random delay, not by a technician who might be busy. Material only runs out at the first station. The machine has a perfect clock, so timestamps never drift or go backwards. There is no quality failure, so every unit that starts is eventually installed. The bottleneck rule (most hours processing or faulted) is simple and would miss a station that is slow because it is often blocked by a neighbor's quirks.

## Why I built this

TODO

## What surprised me

TODO
