import pytest

from conftest import error_items

TRACKING = ("Não consigo acompanhar o rastreio da entrega de o produto. Referente ao pedido #368371. "
            "Obrigado desde já. Aguardo retorno. Prefiro contato por e-mail.")

CASES = [
    ("TK-00042", TRACKING, "delivery", "medium"),
    ("T-PAY", "Fui cobrado duas vezes no cartão, preciso do estorno", "payment", "high"),
    ("T-EXC", "Quero trocar o produto, gostaria de devolver", "exchange_return", "medium"),
    ("T-DEF", "O produto veio quebrado e não funciona", "product_defect", "medium"),
    ("T-LOW", "Gostaria de saber se vocês têm loja física. Dúvida simples.", "other", "low"),
    ("T-OTH", "Bom dia, tudo bem?", "other", "medium"),
]


@pytest.mark.parametrize("ticket_id,text,category,priority", CASES, ids=[c[0] for c in CASES])
def test_classification(client, ticket_id, text, category, priority):
    r = client.post("/tickets/classification", json={"ticket_id": ticket_id, "text": text})
    assert r.status_code == 200
    assert r.json() == {"ticket_id": ticket_id, "category": category, "priority": priority}


def test_missing_text(client):
    r = client.post("/tickets/classification", json={"ticket_id": "T1"})
    assert r.status_code == 422
    assert error_items(r.json()) == [{"type": "missing", "loc": ["body", "text"], "msg": "Field required"}]


def test_empty_text(client):
    r = client.post("/tickets/classification", json={"ticket_id": "T1", "text": ""})
    assert r.status_code == 422
    assert error_items(r.json()) == [{"type": "string_too_short", "loc": ["body", "text"],
                                      "msg": "String should have at least 1 character"}]
