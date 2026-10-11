"""Falhas de provider provocadas no provider-fake: retentativa, fallback, modelo fraco e erro explícito."""
import time

from conftest import DAY, MONTH, T1, T2, calls, chunks, fail, report, start_report, suggestion, wait_report


def assert_fallback(task, primary_large, other_large):
    """No máximo 2 tentativas no large do primário (todas 500), depois sucesso no large do outro provider."""
    seq = calls(task)
    primary = [c for c in seq if c["model"] == primary_large]
    assert 1 <= len(primary) <= 2, seq
    assert all(c["status"] == 500 for c in primary), seq
    assert seq[:-1] == primary, seq
    assert (seq[-1]["model"], seq[-1]["status"]) == (other_large, 200), seq


# ---- primário (provider inteiro) em error_500: fallback técnico --------------------------

def test_f1_primary_down_falls_back_to_other_large(client):
    fail("openai", "error_500")
    r = client.post("/tickets/classification", json=T1)
    assert r.status_code == 200
    assert r.json() == {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}
    assert_fallback("classify", "gpt-fake-large", "claude-fake-large")


def test_f2_primary_down_falls_back_to_other_large(client):
    fail("anthropic", "error_500")
    r, events = suggestion(client, T1)
    assert r.status_code == 200
    assert events[-1][1:] == ("end", {"ticket_id": "TK-00042"})
    assert len(chunks(events)) > 100
    assert_fallback("suggest", "claude-fake-large", "gpt-fake-large")
    assert all(c["stream"] for c in calls("suggest"))


def test_f3_primary_down_falls_back_to_other_large(client):
    fail("anthropic", "error_500")
    assert report(client, DAY)["total_tickets"] == 137
    assert_fallback("topics", "claude-fake-large", "gpt-fake-large")


def test_f4_primary_down_falls_back_to_other_large(client):
    fail("openai", "error_500")
    r = client.post("/tickets/extraction", json=T2)
    assert r.status_code == 200
    assert r.json() == {"ticket_id": "TK-00002", "order_number": "#605065", "product": None}
    assert_fallback("extract", "gpt-fake-large", "claude-fake-large")


# ---- primário pendurado (timeout: 300 s sem bytes) ----------------------------------------

def test_f1_primary_hung_answers_within_15s(client):
    fail("openai", "timeout")
    t0 = time.monotonic()
    r = client.post("/tickets/classification", json=T1)
    elapsed = time.monotonic() - t0
    assert r.status_code in (200, 503), r.text      # sucesso pelo fallback ou erro explícito
    assert elapsed <= 15, f"F1 levou {elapsed:.2f} s"


# ---- os dois LARGE em error_500: modelo fraco onde ele é aceito ---------------------------

def both_large_down():
    fail("openai", "error_500", "gpt-fake-large")
    fail("anthropic", "error_500", "claude-fake-large")


def test_both_large_down_f1_answers_with_mini(client):
    both_large_down()
    r = client.post("/tickets/classification", json=T1)
    assert r.status_code == 200
    last = calls("classify")[-1]
    assert (last["model"], last["status"]) == ("gpt-fake-mini", 200)


def test_both_large_down_f2_streams_to_the_end_with_mini(client):
    both_large_down()
    r, events = suggestion(client, T1)
    assert r.status_code == 200
    assert chunks(events)
    assert events[-1][1:] == ("end", {"ticket_id": "TK-00042"})
    last = calls("suggest")[-1]
    assert (last["model"], last["status"], last["stream"]) == ("claude-fake-mini", 200, True)


def test_both_large_down_f3_report_with_mini(client):
    both_large_down()
    assert report(client, DAY)["total_tickets"] == 137
    last = calls("topics")[-1]
    assert (last["model"], last["status"]) == ("claude-fake-mini", 200)


def test_both_large_down_f4_explicit_503_and_no_mini(client):
    both_large_down()
    r = client.post("/tickets/extraction", json=T2)
    assert r.status_code == 503
    assert "tratamento manual" in r.json()["detail"]
    seq = calls("extract")
    assert seq and all(c["status"] == 500 for c in seq)
    assert not [c for c in seq if "mini" in c["model"]], seq


# ---- os dois PROVIDERS inteiros em error_500: o job do relatório falha, explícito e rápido -

def test_f3_job_fails_with_reason_within_60s_when_both_providers_down(client):
    fail("anthropic", "error_500")
    fail("openai", "error_500")
    r, elapsed = start_report(client, MONTH)
    assert r.status_code == 202 and elapsed <= 1
    s, waited = wait_report(client, r.headers["location"], limit=60)
    body = s.json()
    assert s.status_code == 200 and body["state"] == "failed", body
    assert body["reason"]
    assert waited <= 60
    result = client.get(f"/reports/topics/{body['job_id']}")
    assert result.status_code == 409 and body["reason"] in result.json()["detail"]
    # Os lotes que ainda não tinham começado foram cancelados: nem todos os 34 lotes (cada um
    # com um tamanho de entrada) chegaram ao provider, e nenhum lote novo chega depois do
    # `failed`. Os que já estavam no gateway podem ainda terminar a cadeia de fallback: sem
    # streaming, o gateway não para quando a app desiste.
    batches = {c["input_tokens"] for c in calls("topics")}
    assert len(batches) < 34
    time.sleep(3)
    assert {c["input_tokens"] for c in calls("topics")} == batches
