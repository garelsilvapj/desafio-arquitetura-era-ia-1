"""Critérios de aceitação do desafio, sempre pela borda (BASE_URL) e com falhas provocadas no
provider-fake (PROVIDER_URL). Não importa nada da app: o que vale é o que o cliente vê e o que
o provider registrou em /admin/calls."""
import json
import os
import time

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")

T1 = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}
T2 = {"ticket_id": "TK-00002", "text": ("Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, "
                                        "pedido #605065. Sou cliente da loja há bastante tempo. "
                                        "Fico no aguardo de uma solução.")}
DAY = {"start": "2026-08-01", "end": "2026-08-01"}      # 137 tickets, 1 lote
MONTH = {"start": "2026-08-01", "end": "2026-08-31"}    # 5.000 tickets, 34 lotes


def reset():
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=10).raise_for_status()


@pytest.fixture(autouse=True)
def normal_provider():
    """Provider em modo normal e registro de chamadas vazio antes E depois de cada teste."""
    reset()
    yield
    reset()


def fail(provider, mode, model=None):
    body = {"provider": provider, "mode": mode} | ({"model": model} if model else {})
    httpx.post(f"{PROVIDER_URL}/admin/failures", json=body, timeout=10).raise_for_status()


def calls(task):
    """Chamadas de inferência da tarefa registradas no provider, da mais antiga para a mais recente."""
    items = httpx.get(f"{PROVIDER_URL}/admin/calls", params={"last": 5000}, timeout=10).json()
    return [c for c in reversed(items) if c["task"] == task]


def _never_504(response):
    # Critério do desafio: nenhuma resposta pode ser o 504 da borda.
    assert response.status_code != 504, f"504 em {response.request.method} {response.request.url}"


@pytest.fixture
def client():
    # Timeout do teste acima dos 30 s da borda: um 504 dela aparece como 504, e não como timeout aqui.
    with httpx.Client(base_url=BASE_URL, timeout=40, event_hooks={"response": [_never_504]}) as c:
        yield c


def suggestion(client, ticket):
    """Consome a F2. Devolve (resposta, eventos) com eventos = [(segundos desde o envio, nome, dados)];
    nome None = trecho. Se não vier 200, o corpo JSON fica em resposta.json() e eventos = []."""
    t0 = time.monotonic()
    events, name, data = [], None, []
    with client.stream("POST", "/tickets/reply-suggestion", json=ticket) as r:
        if r.status_code != 200:
            r.read()
            return r, events
        for line in r.iter_lines():
            if line == "":
                if data:
                    events.append((time.monotonic() - t0, name, json.loads("\n".join(data))))
                name, data = None, []
            elif line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").removeprefix(" "))
    return r, events


def chunks(events):
    return [data["chunk"] for _, name, data in events if name is None]


def start_report(client, period):
    t0 = time.monotonic()
    r = client.post("/reports/topics", json=period)
    return r, time.monotonic() - t0


def wait_report(client, status_url, limit):
    """Consulta o estado (a cada 1 s) até o 303 ou `failed`. Devolve (última resposta, segundos)."""
    t0 = time.monotonic()
    while True:
        s = client.get(status_url)
        if s.status_code == 303 or s.json().get("state") == "failed":
            return s, time.monotonic() - t0
        assert s.status_code == 200, s.text
        assert time.monotonic() - t0 < limit, f"job não terminou em {limit} s: {s.json()}"
        time.sleep(1)


def report(client, period, limit=60):
    """Roda um relatório até o fim e devolve o corpo do resultado."""
    r, _ = start_report(client, period)
    assert r.status_code == 202
    s, _ = wait_report(client, r.headers["location"], limit)
    assert s.status_code == 303, s.text
    result = client.get(s.headers["location"])
    assert result.status_code == 200
    return result.json()
