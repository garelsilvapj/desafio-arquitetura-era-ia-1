"""Provisiona as chaves virtuais do gateway (idempotente; só biblioteca padrão).

Roda dentro do container do gateway, disparado pelo entrypoint.sh. O LiteLLM não
aceita chaves declaradas no config.yaml, então elas são criadas pela API de admin:

    espera /health/readiness -> por chave: GET /key/list?key_alias=<alias>
      mesmo hash       -> POST /key/update   (mantém o gasto acumulado)
      ausente          -> POST /key/generate (valor fixo escolhido por nós, vindo do ambiente)
      alias com outra  -> POST /key/delete + /key/generate (o valor mudou no .env)
    -> confere se todo model_name do config.yaml virou deployment (GET /v1/models)
    -> cria /tmp/keys.ok (o healthcheck do compose exige esse arquivo)

Falhas transitórias (gateway subindo, banco ocupado, 5xx) são repetidas até o prazo.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

BASE_URL = "http://127.0.0.1:4000"
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
MARKER = Path("/tmp/keys.ok")
CONFIG = Path(__file__).with_name("config.yaml")
DEADLINE_SECONDS = 180

# As chaves são dados: para criar outra chave (ou dar orçamento/limite a uma existente),
# acrescente um item ou campos aceitos por /key/generate (max_budget, budget_duration,
# rpm_limit, ...). "key" é o valor que o cliente usa; "key_alias" identifica a chave.
# Orçamento e limite são checados pelo gateway ANTES de chamar o provider: a recusa é um
# 429 do próprio gateway e não chega ao provider (nem aparece em /admin/calls).
KEYS = [
    {
        # A app: só as 4 capacidades (os grupos internos <cap>-fallback/-weak ficam fora;
        # o fallback interno os usa mesmo assim). Teto mensal de US$ 80,00 = ~1,5x o gasto
        # estimado do mês (~US$ 52: 5.000 tickets em F1+F2+F4 e 4 relatórios F3), para
        # absorver retentativas, fallbacks e crescimento. Sem rpm: a F3 dispara 34 lotes
        # de uma vez (concorrência 4) e um rpm baixo derrubaria o relatório.
        "key_alias": "helpdesk-app",
        "key": os.environ["GATEWAY_APP_KEY"],
        "models": ["classify", "suggest", "topics", "extract"],
        "max_budget": 80.0,
        "budget_duration": "30d",
    },
    {
        # Demonstração de orçamento (scripts/governance-demo.sh). O gateway recusa quando o
        # gasto acumulado >= teto, e a 1ª chamada sempre passa (gasto 0). O teto precisa
        # ser menor que o custo de UMA classify do roteiro em qualquer modelo que a
        # capacidade possa usar: US$ 0,0001575 no gpt-fake-large (19 tokens de entrada,
        # 11 de saída) e US$ 0,00000945 no gpt-fake-mini, o mais barato. Com US$ 0,000005,
        # a 2ª chamada é recusada mesmo com o modelo da classify trocado. O roteiro zera o
        # gasto antes de começar.
        "key_alias": "demo-budget",
        "key": os.environ["GATEWAY_DEMO_BUDGET_KEY"],
        "models": ["classify"],
        "max_budget": 0.000005,
    },
    {
        # Demonstração de limite de taxa: 2 requisições por minuto; a 3ª é recusada.
        "key_alias": "demo-rpm",
        "key": os.environ["GATEWAY_DEMO_RPM_KEY"],
        "models": ["classify"],
        "rpm_limit": 2,
    },
]


class Transient(Exception):
    """Falha que vale tentar de novo (conexão recusada, 5xx, timeout)."""


def log(message: str) -> None:
    print(f"[provision_keys] {message}", flush=True)


def request(method: str, path: str, body: dict | None = None, auth: bool = True) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", f"Bearer {MASTER_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        payload = err.read()
        if err.code >= 500:
            raise Transient(f"{method} {path} -> {err.code}: {payload[:200]!r}") from err
        try:
            return err.code, json.loads(payload or b"{}")
        except ValueError:
            return err.code, {"raw": payload.decode(errors="replace")[:200]}
    except (urllib.error.URLError, ConnectionError, TimeoutError) as err:
        raise Transient(f"{method} {path}: {err}") from err


def wait_ready() -> None:
    status, _ = request("GET", "/health/readiness", auth=False)
    if status != 200:
        raise Transient(f"/health/readiness -> {status}")


def call(method: str, path: str, body: dict | None = None) -> dict:
    status, payload = request(method, path, body)
    if status != 200:
        raise RuntimeError(f"{method} {path} -> {status} {payload}")
    return payload


def upsert(spec: dict) -> str:
    alias = spec["key_alias"]
    token = hashlib.sha256(spec["key"].encode()).hexdigest()  # o LiteLLM guarda só o hash
    # Busca pelo alias (e não /key/info), para a ausência não virar ERROR no log do gateway.
    found = call("GET", f"/key/list?key_alias={quote(alias)}&return_full_object=true")
    existing = [k for k in found.get("keys", []) if k.get("key_alias") == alias]

    if any(k.get("token") == token for k in existing):
        call("POST", "/key/update", spec)
        spend = next(k.get("spend") for k in existing if k.get("token") == token)
        return f"atualizada (gasto preservado: US$ {spend})"
    if existing:
        # O alias aponta para outra chave (o valor mudou no .env): remove a antiga e recria.
        log(f"{alias}: alias usado por outra chave; removendo a antiga (rotação)")
        call("POST", "/key/delete", {"key_aliases": [alias]})
    call("POST", "/key/generate", spec)
    return "criada"


def check_deployments() -> list[str]:
    """Todo model_name do config.yaml precisa ter virado deployment no LiteLLM.

    Na subida, o proxy descarta EM SILÊNCIO um deployment inválido (ex.: erro de digitação
    no provider de `model:`). A capacidade passa a ser atendida pelo fallback, e o
    /v1/models da chave da app continua listando o nome (ele devolve a allowlist da chave).
    Aqui a falta vira erro: sem /tmp/keys.ok o gateway não fica healthy.
    """
    declared = sorted({name.strip("'\"") for name in re.findall(r"^\s*-\s*model_name:\s*(\S+)", CONFIG.read_text(), re.M)})
    loaded = {model["id"] for model in call("GET", "/v1/models").get("data", [])}
    missing = [name for name in declared if name not in loaded]
    if missing:
        raise RuntimeError(
            f"deployment(s) do {CONFIG.name} descartado(s) pelo LiteLLM na subida: {missing}; "
            "procure 'Error creating deployment' neste log"
        )
    return declared


def retry(step, what: str, deadline: float):
    delay = 1.0
    while True:
        try:
            return step()
        except Transient as err:
            if time.monotonic() > deadline:
                raise RuntimeError(f"{what}: prazo de {DEADLINE_SECONDS}s esgotado ({err})") from err
            log(f"{what}: ainda não ({err}); nova tentativa em {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def main() -> int:
    started = time.monotonic()
    deadline = started + DEADLINE_SECONDS
    log(f"aguardando o gateway em {BASE_URL}/health/readiness")
    try:
        retry(wait_ready, "readiness", deadline)
        log(f"gateway pronto em {time.monotonic() - started:.1f}s")
        for spec in KEYS:
            result = retry(lambda: upsert(spec), spec["key_alias"], deadline)
            log(f"chave {spec['key_alias']}: {result} (models={spec.get('models')})")
        deployments = retry(check_deployments, "deployments", deadline)
        log(f"{len(deployments)} deployment(s) do {CONFIG.name} carregado(s): {', '.join(deployments)}")
    except Exception as err:  # noqa: BLE001 - qualquer falha deixa o container unhealthy
        log(f"ERRO: {err}; /tmp/keys.ok não foi criado (healthcheck vai falhar)")
        return 1
    MARKER.touch()
    log(f"{len(KEYS)} chave(s) provisionada(s) em {time.monotonic() - started:.1f}s; {MARKER} criado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
