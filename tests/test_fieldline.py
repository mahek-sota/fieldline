import random

import pytest
from fastapi.testclient import TestClient

import sim
from api.main import app

SEED = 7


@pytest.fixture(scope="module")
def run():
    return sim.simulate(SEED)


def post_all(client, events, batch=500):
    for i in range(0, len(events), batch):
        r = client.post("/events", json=events[i:i + batch])
        assert r.status_code == 200


def report_from(tmp_path, monkeypatch, name, events):
    monkeypatch.setenv("FIELDLINE_DB", str(tmp_path / f"{name}.db"))
    client = TestClient(app)
    post_all(client, events)
    return client.get("/report").json()


def test_sending_every_event_twice_gives_same_report(run, tmp_path, monkeypatch):
    events, _ = run
    once = report_from(tmp_path, monkeypatch, "once", events)

    twice = events + events
    random.Random(1).shuffle(twice)           # duplicates and out-of-order arrival
    doubled = report_from(tmp_path, monkeypatch, "twice", twice)

    assert doubled == once


def test_drift_alert_fires_before_planted_fault(run, tmp_path, monkeypatch):
    events, truth = run
    report = report_from(tmp_path, monkeypatch, "drift", events)
    alarms = [a for a in report["drift"]["alarms"] if a["station"] == truth["station"]]

    assert alarms, "no drift alarm on the drifting station"
    first = alarms[0]
    assert truth["drift_started"] < first["alarm_at"] < truth["ts"]
    assert first["fixed_by_fault"]["reason"] == truth["reason"]
    assert first["warning_hours"] > 0
    # no alarms on stations that were not drifting
    assert all(a["station"] == truth["station"] for a in report["drift"]["alarms"])


def test_same_seed_gives_same_simulation():
    a_events, a_truth = sim.simulate(SEED)
    b_events, b_truth = sim.simulate(SEED)
    assert a_events == b_events
    assert a_truth == b_truth
    c_events, _ = sim.simulate(SEED + 1)
    assert c_events != a_events
