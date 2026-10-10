import json
from pathlib import Path

from conftest import error_items

GOLDEN = Path(__file__).parent / "golden"


def test_single_day_report(client):
    r = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-01"})
    assert r.status_code == 200
    expected = json.loads((GOLDEN / "topics_2026-08-01.json").read_text(encoding="utf-8"))
    assert expected["total_tickets"] == 137
    assert r.json() == expected


def test_empty_period(client):
    r = client.post("/reports/topics", json={"start": "2026-09-01", "end": "2026-09-30"})
    assert r.status_code == 200
    assert r.json() == {"start": "2026-09-01", "end": "2026-09-30", "total_tickets": 0, "topics": []}


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
