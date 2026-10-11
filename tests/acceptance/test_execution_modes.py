"""Modo de execução de cada feature, em operação normal (e o stream da F2 interrompido)."""
import re
import time

from conftest import MONTH, T1, calls, chunks, fail, start_report, suggestion, wait_report


def test_f1_classification_complete_within_3s(client):
    t0 = time.monotonic()
    r = client.post("/tickets/classification", json=T1)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert r.json() == {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}
    assert elapsed <= 3, f"F1 levou {elapsed:.2f} s"


def test_f2_first_chunk_fast_and_text_keeps_arriving(client):
    r, events = suggestion(client, T1)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    texts = chunks(events)
    first, last_chunk, end = events[0][0], events[-2][0], events[-1][0]
    # 1º trecho de texto em até 1,5 s, bem antes da metade do total.
    assert events[0][1] is None and texts[0]
    assert first <= 1.5, f"1º trecho aos {first:.2f} s"
    assert first < end / 2, f"1º trecho aos {first:.2f} s, fim aos {end:.2f} s"
    # O texto continua chegando até o `end`: muitos trechos, espalhados no tempo.
    assert len(texts) > 100
    assert last_chunk - first > end / 2
    assert events[-1][1:] == ("end", {"ticket_id": "TK-00042"})
    assert all(name is None for _, name, _ in events[:-1])


def test_f2_interrupted_after_content_sends_error_event(client):
    fail("anthropic", "midstream_error")    # primário da F2: entrega ~1/4 do texto e quebra
    r, events = suggestion(client, T1)
    assert r.status_code == 200
    assert len(chunks(events)) > 0
    # Último evento é o erro no formato do contrato, e o stream termina (o `with` saiu do laço).
    assert events[-1][1:] == ("error", {"ticket_id": "TK-00042", "message": "A geração foi interrompida"})
    assert all(name is None for _, name, _ in events[:-1])


def test_f2_failure_before_any_content_is_plain_503(client):
    fail("anthropic", "error_500")
    fail("openai", "error_500")
    r, events = suggestion(client, T1)
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/json")
    assert "indisponível" in r.json()["detail"]
    assert events == []


def test_f3_month_is_accepted_at_once_and_finishes_in_background(client):
    r, elapsed = start_report(client, MONTH)
    assert elapsed <= 1, f"POST levou {elapsed:.2f} s"
    assert r.status_code == 202
    assert int(r.headers["retry-after"]) > 0
    status_url = r.headers["location"]
    job_id = status_url.removeprefix("/reports/topics/status/")
    assert job_id and r.json()["job_id"] == job_id

    # Enquanto roda: 200 com estado e progresso; o resultado ainda não existe.
    s = client.get(status_url)
    assert s.status_code == 200
    assert s.json()["state"] in ("pending", "running")
    assert re.fullmatch(r"\d+/5000", s.json()["progress"])
    assert client.get(f"/reports/topics/{job_id}").status_code == 409

    s, _ = wait_report(client, status_url, limit=240)
    assert s.status_code == 303
    assert s.headers["location"] == f"/reports/topics/{job_id}"
    result = client.get(s.headers["location"])
    assert result.status_code == 200
    body = result.json()
    assert body["total_tickets"] == 5000
    assert (body["start"], body["end"]) == (MONTH["start"], MONTH["end"])
    assert sum(t["count"] for t in body["topics"]) == 5000

    # As chamadas topics que produziram o relatório: 34 lotes de 150, todas 200.
    topics = calls("topics")
    assert len(topics) == 34
    assert all(c["status"] == 200 for c in topics)


def test_f3_unknown_job_is_404(client):
    assert client.get("/reports/topics/status/nao-existe").status_code == 404
    assert client.get("/reports/topics/nao-existe").status_code == 404

