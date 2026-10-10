import os

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")


@pytest.fixture(scope="session", autouse=True)
def provider_normal_mode():
    """Garante o provedor em modo normal antes da sessão."""
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=10).raise_for_status()


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        yield c


def error_items(body):
    """Reduz os itens de erro do pydantic aos campos estáveis."""
    return [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in body["detail"]]
