import pytest

from conftest import error_items

TK2 = ("Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065. "
       "Sou cliente da loja há bastante tempo. Fico no aguardo de uma solução.")

CASES = [
    ("TK-00002", TK2, "#605065", None),
    ("E-PROD", "Quero trocar a air fryer do pedido #123456", "#123456", "air fryer"),
    ("E-NONE", "Olá, vocês entregam em Curitiba?", None, None),
]


@pytest.mark.parametrize("ticket_id,text,order,product", CASES, ids=[c[0] for c in CASES])
def test_extraction(client, ticket_id, text, order, product):
    r = client.post("/tickets/extraction", json={"ticket_id": ticket_id, "text": text})
    assert r.status_code == 200
    assert r.json() == {"ticket_id": ticket_id, "order_number": order, "product": product}


def test_empty_text(client):
    r = client.post("/tickets/extraction", json={"ticket_id": "T1", "text": ""})
    assert r.status_code == 422
    assert error_items(r.json()) == [{"type": "string_too_short", "loc": ["body", "text"],
                                      "msg": "String should have at least 1 character"}]
