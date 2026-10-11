import json
import time
from pathlib import Path

from conftest import error_items

GOLDEN = Path(__file__).parent / "golden"


def run_report(client, period):
    """Desde a fase 6 o relatório é assíncrono: 202 + Location, consulta do estado até o 303, resultado."""
    r = client.post("/reports/topics", json=period)
    assert r.status_code == 202
    assert int(r.headers["retry-after"]) > 0
    status_url = r.headers["location"]
    assert status_url.startswith("/reports/topics/status/")
    deadline = time.monotonic() + 60
    while True:
        s = client.get(status_url)
        if s.status_code == 303:
            break
        assert s.status_code == 200
        assert s.json()["state"] in ("pending", "running")
        assert time.monotonic() < deadline, f"relatório não ficou pronto em 60 s: {s.json()}"
        time.sleep(1)
    result = client.get(s.headers["location"])
    assert result.status_code == 200
    return result.json()


def test_single_day_report(client):
    expected = json.loads((GOLDEN / "topics_2026-08-01.json").read_text(encoding="utf-8"))
    assert expected["total_tickets"] == 137
    assert run_report(client, {"start": "2026-08-01", "end": "2026-08-01"}) == expected


def test_empty_period(client):
    report = run_report(client, {"start": "2026-09-01", "end": "2026-09-30"})
    assert report == {"start": "2026-09-01", "end": "2026-09-30", "total_tickets": 0, "topics": []}


def test_start_after_end(client):
    r = client.post("/reports/topics", json={"start": "2026-08-02", "end": "2026-08-01"})
    assert r.status_code == 422
    assert r.json() == {"detail": "A data inicial é posterior à data final"}


def test_invalid_date(client):
    r = client.post("/reports/topics", json={"start": "abc", "end": "2026-08-01"})
    assert r.status_code == 422
    assert error_items(r.json()) == [{
        "type": "date_from_datetime_parsing", "loc": ["body", "start"],
        "msg": "Input should be a valid date or datetime, input is too short"}]
