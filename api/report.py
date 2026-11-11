"""Turn a set of events into the site report.

A pure function of the event set: order of arrival does not matter because
events are sorted by (ts, event_id) first, and duplicates are removed by id.
"""
import math
from collections import defaultdict
from datetime import datetime

STATION_ORDER = ["stage", "assemble", "qc", "install"]
LAST_STATION = "install"

# CUSUM settings. Cycle times are grouped into batches of BATCH consecutive
# units per station; the CUSUM runs on standardized batch means.
BATCH = 30
CUSUM_K = 1.0       # slack, in standard errors of a batch mean (absorbs baseline error)
CUSUM_H = 5.0       # alarm threshold, same units
CONFIRM_BATCHES = 6 # batches under threshold after a repair before we call it fixed


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hours(seconds):
    return round(seconds / 3600, 2)


def build_report(events):
    events = sorted({e["event_id"]: e for e in events}.values(), key=lambda e: (e["ts"], e["event_id"]))
    if not events:
        return {"events": 0}
    stations = sorted({e["station"] for e in events}, key=lambda s: STATION_ORDER.index(s) if s in STATION_ORDER else 99)
    first_day = events[0]["ts"][:10]

    units_per_day = defaultdict(int)
    cycles = defaultdict(list)                      # station -> [(ts, cycle_s)]
    fault_starts = {}
    faults = []                                     # closed faults
    material = []
    for e in events:
        t = e["type"]
        if t == "unit_completed":
            cycles[e["station"]].append((e["ts"], e["cycle_s"]))
            if e["station"] == LAST_STATION:
                units_per_day[e["ts"][:10]] += 1
        elif t == "fault_started":
            fault_starts[e["fault_id"]] = e
        elif t == "fault_cleared":
            start = fault_starts.pop(e["fault_id"], None)
            if start:
                dur = (parse_ts(e["ts"]) - parse_ts(start["ts"])).total_seconds()
                faults.append({"station": e["station"], "reason": e["reason"], "started": start["ts"],
                               "cleared": e["ts"], "duration_s": dur})
        elif t == "waiting_for_material":
            material.append(e)

    # Lost time
    by_reason = defaultdict(lambda: {"count": 0, "seconds": 0.0})
    station_fault_s = defaultdict(float)
    station_material_s = defaultdict(float)
    for f in faults:
        by_reason[f["reason"]]["count"] += 1
        by_reason[f["reason"]]["seconds"] += f["duration_s"]
        station_fault_s[f["station"]] += f["duration_s"]
    for m in material:
        station_material_s[m["station"]] += m["duration_s"]
    fault_total = sum(station_fault_s.values())
    material_total = sum(station_material_s.values())
    lost_time = {
        "total_hours": hours(fault_total + material_total),
        "machine_fault_hours": hours(fault_total),
        "waiting_for_material_hours": hours(material_total),
        "by_reason": sorted(
            [{"reason": r, "category": "machine_fault", "count": v["count"], "hours": hours(v["seconds"])}
             for r, v in by_reason.items()]
            + [{"reason": "LATE_MATERIAL", "category": "waiting_for_material", "count": len(material),
                "hours": hours(material_total)}],
            key=lambda x: -x["hours"]),
        "open_faults": [{"station": s["station"], "reason": s["reason"], "started": s["ts"]}
                        for s in fault_starts.values()],
    }

    # Bottleneck: the station that spends the most time either working or broken.
    # Every other station spends more of its time starved or blocked by it.
    station_rows = []
    for s in stations:
        busy = sum(c for _, c in cycles[s])
        station_rows.append({
            "station": s,
            "units": len(cycles[s]),
            "mean_cycle_s": round(busy / len(cycles[s]), 1) if cycles[s] else None,
            "busy_hours": hours(busy),
            "fault_hours": hours(station_fault_s[s]),
            "material_wait_hours": hours(station_material_s[s]),
            "load_hours": hours(busy + station_fault_s[s]),
        })
    top = max(station_rows, key=lambda r: r["load_hours"])
    bottleneck = {"station": top["station"], "load_hours": top["load_hours"],
                  "rule": "station with the most hours spent processing or faulted"}

    drift = cusum_report(stations, cycles, faults, first_day, [e["ts"] for e in events])

    return {
        "site": {"start": events[0]["ts"], "end": events[-1]["ts"], "events": len(events)},
        "units_per_day": [{"date": d, "units": n} for d, n in sorted(units_per_day.items())],
        "lost_time": lost_time,
        "stations": station_rows,
        "bottleneck": bottleneck,
        "drift": drift,
    }


def cusum_report(stations, cycles, faults, first_day, all_ts):
    """One-sided CUSUM per station on batch means of cycle time.

    Batches never span a fault. An alarm stays open until a repair fixes the
    problem: after each repair the CUSUM restarts from zero, and if it stays
    under the threshold for CONFIRM_BATCHES batches, that repair is the fix.
    If it crosses the threshold again, the repair did not help and the same
    alarm stays open. Warning time = alarm to the start of the fixing fault.
    """
    alarms = []
    series = {}
    for s in stations:
        cs = cycles[s]
        base = [c for ts, c in cs if ts[:10] == first_day]
        if len(base) < 2 * BATCH:
            continue
        mu = sum(base) / len(base)
        sd = math.sqrt(sum((c - mu) ** 2 for c in base) / (len(base) - 1))
        se = sd / math.sqrt(BATCH)
        st_faults = sorted((f for f in faults if f["station"] == s), key=lambda f: f["cleared"])

        S, episode, pending_fix, ok_batches, batch, pts, fi = 0.0, None, None, 0, [], [], 0
        for ts, c in cs:
            while fi < len(st_faults) and st_faults[fi]["cleared"] <= ts:
                f = st_faults[fi]
                fi += 1
                batch = []                      # do not mix cycles from before and after a stop
                if episode:
                    episode["faults_during"].append(f)
                    # A pending fix that has already been followed by normal batches keeps the
                    # credit. With no evidence yet, the newer repair takes over.
                    if not pending_fix or ok_batches == 0:
                        S, pending_fix, ok_batches = 0.0, f, 0
            batch.append(c)
            if len(batch) < BATCH:
                continue
            mean = sum(batch) / BATCH
            batch = []
            S = max(0.0, S + (mean - mu) / se - CUSUM_K)
            pts.append({"ts": ts, "mean_cycle_s": round(mean, 2), "cusum": round(S, 2)})
            if not episode:
                if S > CUSUM_H:
                    episode = {"station": s, "alarm_at": ts, "baseline_cycle_s": round(mu, 1),
                               "batch_mean_at_alarm_s": round(mean, 1), "faults_during": []}
            elif pending_fix:
                if S > CUSUM_H:
                    pending_fix = None          # that repair did not fix it
                else:
                    ok_batches += 1
                    if ok_batches >= CONFIRM_BATCHES:
                        alarms.append(close_episode(episode, pending_fix, ts, all_ts))
                        episode, pending_fix, S = None, None, 0.0
        if episode:
            alarms.append(close_episode(episode, None, None, all_ts))
        series[s] = pts

    return {
        "method": f"one-sided CUSUM on means of {BATCH} consecutive cycles per station; baseline = first day; "
                  f"k = {CUSUM_K}, h = {CUSUM_H} (standard errors)",
        "alarms": alarms,
        "series": series,
    }


def close_episode(ep, fix, back_to_normal_at, all_ts):
    alarm_ts = parse_ts(ep["alarm_at"])
    out = {k: ep[k] for k in ("station", "alarm_at", "baseline_cycle_s", "batch_mean_at_alarm_s")}
    out["back_to_normal_at"] = back_to_normal_at
    out["fixed_by_fault"] = None
    out["warning_hours"] = None
    out["warning_operating_hours"] = None
    if fix:
        out["fixed_by_fault"] = {"started": fix["started"], "reason": fix["reason"],
                                 "repair_hours": hours(fix["duration_s"])}
        out["warning_hours"] = hours((parse_ts(fix["started"]) - alarm_ts).total_seconds())
        out["warning_operating_hours"] = hours(operating_seconds(ep["alarm_at"], fix["started"], all_ts))
    out["other_faults_during_alarm"] = [{"started": f["started"], "reason": f["reason"]}
                                        for f in ep["faults_during"] if f is not fix]
    return out


def operating_seconds(a, b, all_ts, idle_gap_s=2 * 3600):
    """Seconds between a and b, minus any stretch longer than idle_gap_s with no events (nights)."""
    pts = [parse_ts(t) for t in all_ts if a <= t <= b]
    total = (parse_ts(b) - parse_ts(a)).total_seconds()
    for x, y in zip(pts, pts[1:]):
        gap = (y - x).total_seconds()
        if gap > idle_gap_s:
            total -= gap
    return total
