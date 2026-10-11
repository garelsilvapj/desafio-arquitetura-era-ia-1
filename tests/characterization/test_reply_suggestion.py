import json
from pathlib import Path

import pytest

from conftest import error_items

GOLDEN = Path(__file__).parent / "golden"

TICKETS = {
    "TK-00002": ("Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065. "
                 "Sou cliente da loja há bastante tempo. Fico no aguardo de uma solução."),
    "TK-00042": ("Não consigo acompanhar o rastreio da entrega de o produto. Referente ao pedido #368371. "
                 "Obrigado desde já. Aguardo retorno. Prefiro contato por e-mail."),
}


def read_events(response):
    """Eventos SSE na ordem: (nome, dados JSON); nome None = mensagem sem `event:` (trecho)."""
    events, name, data = [], None, []
    for line in response.iter_lines():
        if line == "":
            if data:
                events.append((name, json.loads("\n".join(data))))
            name, data = None, []
        elif line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").removeprefix(" "))
    return events


# Desde a fase 6 a sugestão chega em streaming (SSE); o texto completo é o mesmo da v1.
@pytest.mark.parametrize("ticket_id", sorted(TICKETS))
def test_reply_suggestion(client, ticket_id):
    with client.stream("POST", "/tickets/reply-suggestion",
                       json={"ticket_id": ticket_id, "text": TICKETS[ticket_id]}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = read_events(r)
    expected = json.loads((GOLDEN / f"reply_{ticket_id}.json").read_text(encoding="utf-8"))
    chunks = events[:-1]
    assert len(chunks) > 1
    assert all(name is None and set(data) == {"chunk"} for name, data in chunks)
    assert "".join(data["chunk"] for _, data in chunks) == expected["suggestion"]
    assert events[-1] == ("end", {"ticket_id": ticket_id})


def test_missing_ticket_id(client):
    r = client.post("/tickets/reply-suggestion", json={"text": "x"})
    assert r.status_code == 422
    assert error_items(r.json()) == [{"type": "missing", "loc": ["body", "ticket_id"], "msg": "Field required"}]
