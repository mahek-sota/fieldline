"""Simulated field machine: four stations in a row with small buffers.

Writes one JSON event per line to data/events.jsonl (in the order the machine
would emit them) and the planted fault to data/truth.json.

Usage: python sim.py [--seed 7] [--out data]
"""
import argparse
import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import simpy

STATIONS = ["stage", "assemble", "qc", "install"]
MEAN_CYCLE_S = {"stage": 52.0, "assemble": 58.0, "qc": 45.0, "install": 62.0}
CYCLE_SPREAD = 0.15          # lognormal sigma
BUFFER_SIZE = 3

DAYS = 5
SHIFT_START_H = 7
SHIFT_S = 10 * 3600          # one 10 hour shift per day; nights are skipped
SITE_START = datetime(2026, 6, 1, tzinfo=timezone.utc)

MTBF_S = 8 * 3600            # random faults, per station, in operating time
REPAIR_S = (5 * 60, 40 * 60)
FAULT_REASONS = ["JAM", "SENSOR", "TORQUE_FAULT", "ESTOP"]

PALLET_UNITS = 80            # stage needs a new pallet of parts every 80 units
LATE_DELIVERY_P = 0.25
LATE_WAIT_S = (20 * 60, 90 * 60)

# Planted problem: assemble slowly gets slower, then fails.
DRIFT_STATION = "assemble"
DRIFT_START_S = 1 * SHIFT_S + 3 * 3600      # day 2, 10:00
DRIFT_LEN_S = 2 * SHIFT_S                   # two shifts of creeping slowdown
DRIFT_MAX = 0.30                            # +30% cycle time at the end
DRIFT_FAULT_REASON = "BEARING_WEAR"
DRIFT_REPAIR_S = 3 * 3600


def to_wall_clock(t):
    """Map operating seconds to a timestamp. Each day is one shift."""
    day, offset = divmod(t, SHIFT_S)
    return SITE_START + timedelta(days=int(day), hours=SHIFT_START_H, seconds=offset)


def iso(dt):
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Machine:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.id_rng = random.Random(seed + 1_000_003)
        self.env = simpy.Environment()
        self.events = []
        self.truth = {}
        self.buffers = [simpy.Store(self.env, capacity=BUFFER_SIZE) for _ in STATIONS[:-1]]
        self.next_unit = 1
        self.drift_fault_done = False
        for i, name in enumerate(STATIONS):
            self.env.process(self.station(i, name))

    def new_id(self):
        return str(uuid.UUID(int=self.id_rng.getrandbits(128), version=4))

    def emit(self, type_, station, t=None, **fields):
        t = self.env.now if t is None else t
        ev = {"event_id": self.new_id(), "ts": iso(to_wall_clock(t)), "station": station, "type": type_}
        ev.update(fields)
        self.events.append(ev)
        return ev

    def drift_factor(self, name):
        if name != DRIFT_STATION or self.drift_fault_done:
            return 1.0
        progress = (self.env.now - DRIFT_START_S) / DRIFT_LEN_S
        return 1.0 + DRIFT_MAX * min(max(progress, 0.0), 1.0)

    def cycle_time(self, name):
        mean = MEAN_CYCLE_S[name] * self.drift_factor(name)
        mu = math.log(mean) - CYCLE_SPREAD ** 2 / 2
        return self.rng.lognormvariate(mu, CYCLE_SPREAD)

    def fault(self, name, reason, repair_s):
        started = self.emit("fault_started", name, reason=reason, fault_id=None)
        started["fault_id"] = started["event_id"]
        yield self.env.timeout(repair_s)
        self.emit("fault_cleared", name, reason=reason, fault_id=started["event_id"])

    def station(self, i, name):
        env = self.env
        next_fault_at = self.rng.expovariate(1 / MTBF_S)
        pallet_left = PALLET_UNITS
        while True:
            # Pull a unit from upstream (stage creates them).
            if i == 0:
                if pallet_left == 0:
                    if self.rng.random() < LATE_DELIVERY_P:
                        wait = self.rng.uniform(*LATE_WAIT_S)
                        start = env.now
                        yield env.timeout(wait)
                        self.emit("waiting_for_material", name, t=start, duration_s=round(wait, 1))
                    pallet_left = PALLET_UNITS
                pallet_left -= 1
                unit = self.next_unit
                self.next_unit += 1
            else:
                unit = yield self.buffers[i - 1].get()

            # Faults happen between cycles.
            if name == DRIFT_STATION and not self.drift_fault_done and env.now >= DRIFT_START_S + DRIFT_LEN_S:
                self.drift_fault_done = True
                self.truth = {"station": name, "reason": DRIFT_FAULT_REASON, "ts": iso(to_wall_clock(env.now)),
                              "drift_started": iso(to_wall_clock(DRIFT_START_S))}
                yield from self.fault(name, DRIFT_FAULT_REASON, DRIFT_REPAIR_S)
                next_fault_at = env.now + self.rng.expovariate(1 / MTBF_S)
            if env.now >= next_fault_at:
                reason = self.rng.choice(FAULT_REASONS)
                yield from self.fault(name, reason, self.rng.uniform(*REPAIR_S))
                next_fault_at = env.now + self.rng.expovariate(1 / MTBF_S)

            ct = self.cycle_time(name)
            yield env.timeout(ct)
            self.emit("unit_completed", name, unit_id=unit, cycle_s=round(ct, 2))

            if i < len(STATIONS) - 1:
                yield self.buffers[i].put(unit)   # blocks while downstream buffer is full

    def run(self):
        self.env.run(until=DAYS * SHIFT_S)
        return self.events, self.truth


def simulate(seed=7):
    return Machine(seed).run()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data")
    args = p.parse_args()
    events, truth = simulate(args.seed)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    with open(out / "events.jsonl", "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    counts = {}
    for ev in events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    print(f"sim: seed={args.seed} events={len(events)} {counts}")
    print(f"sim: planted fault {truth}")


if __name__ == "__main__":
    main()
