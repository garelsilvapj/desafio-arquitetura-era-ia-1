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


@pytest.mark.parametrize("ticket_id", sorted(TICKETS))
def test_reply_suggestion(client, ticket_id):
    r = client.post("/tickets/reply-suggestion", json={"ticket_id": ticket_id, "text": TICKETS[ticket_id]})
    assert r.status_code == 200
    expected = json.loads((GOLDEN / f"reply_{ticket_id}.json").read_text(encoding="utf-8"))
    assert r.json() == expected


def test_missing_ticket_id(client):
    r = client.post("/tickets/reply-suggestion", json={"text": "x"})
    assert r.status_code == 422
    assert error_items(r.json()) == [{"type": "missing", "loc": ["body", "ticket_id"], "msg": "Field required"}]
