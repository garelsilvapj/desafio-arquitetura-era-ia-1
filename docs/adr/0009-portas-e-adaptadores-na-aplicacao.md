# 0009. Fazer as features dependerem de capacidades (ports), com adapters injetados pelo ponto de composição

- Status: aceita
- Data: 2026-10-09
- Nível: software

## Contexto

Com o gateway ([ADR 0002](0002-ai-gateway-self-hosted.md)), provider, modelo físico e política de chamada saem da app. Falta decidir como o código da app chega ao gateway. Na v1 (`metrics/results/v1-coupled.csv` e [plano de refatoração](../refactoring-plan.md)):

- **`llm`** (Ca = 4, D = 0,80, zona de dor) importava os dois SDKs e criava os clientes no import, com as chaves dos providers. Expunha `call_openai`/`call_anthropic`, então a feature escolhia o provider pelo nome da função.
- **`config`** (Ca = 6, D = 1,00) era lido por 6 dos 9 módulos e misturava segredos, URLs e nomes físicos de modelo por feature.
- **As features importavam `config` e `llm`.** Eram 9 arestas de feature para módulo concreto e volátil, contadas com o parser do `coupling.py`.
- **Testar exigia rede ou monkeypatch.** Uma regra da feature, como o 502 da classificação fora dos valores, só podia ser testada com rede ou trocando um global de módulo.

As próximas fases precisam de streaming na F2 e de lotes concorrentes na F3, o que pede chamadas assíncronas.

## Opções consideradas

1. **Manter `llm.py` com uma função por provider** (a v1), trocando só o destino. A feature continua escolhendo o provider pelo nome da função, e `llm` continua na zona de dor.
2. **Um módulo fino de cliente do gateway, importado pelas features** (ex.: `gateway_client.complete(...)`).
   - O provider some do nome da função.
   - A feature continua dependendo de um módulo concreto e volátil: SDK, URL, chave e timeout lidos de `config`.
   - Pela fórmula, com Ca = 4, Ce = 1 (`config`) e A = 0, ele teria I = 0,20 e D = 0,80, os números do `llm`.
   - Testar a feature continua exigindo monkeypatch.
3. **Ports e adapters, com ponto de composição.** A feature depende de uma abstração da capacidade, o adapter concreto a implementa e só `main` conhece os dois.

## Decisão

Opção 3. As peças:

- **`ports.py`**: dois `typing.Protocol` assíncronos.
  - `LanguageModel.complete(capability, task, content) -> str` separa o roteamento (a capacidade, que o gateway resolve) do protocolo da tarefa (`TASK: <task>` no prompt).
  - `TicketSource.in_period(start, end) -> list[dict]` é assíncrono porque a rota da F3 roda no event loop (R9).
- **`adapters/gateway.py`**: `GatewayLanguageModel(LanguageModel)`, o **único** componente que chama o gateway e importa `openai`.
  - Cria o cliente com `AsyncOpenAI(..., max_retries=0, timeout=...)`: quem repete é o gateway (ADR 0006), e o timeout de 25 s é só rede de segurança abaixo dos 30 s da borda.
  - Manda `model=<capacidade>` e sempre `max_tokens`.
  - Nunca envia política (`timeout`/`num_retries` no corpo, headers `x-litellm-*`), que o LiteLLM aplicaria por cima da política do gateway. Os `x-stainless-*` que o SDK envia não são lidos como política nem repassados ao provider (`proxy/litellm_pre_call_utils.py` do LiteLLM).
  - URL, chave, timeout e `max_tokens` chegam pelo construtor: o adapter não importa `config`.
- **`adapters/tickets_file.py`**: `FileTicketSource(TicketSource)`. Lê o arquivo em `asyncio.to_thread`, porque a leitura bloqueante leva 8–11 ms de mediana e até 20 ms (R9).
- **Features**: recebem os ports por parâmetro e declaram a própria capacidade lógica: `CAPABILITY = "classify"`, `"suggest"`, `"topics"` ou `"extract"` (R4), e `report.TICKETS_PER_CALL = 150` (R8). Da app, importam só `ports` e `schemas`.
- **`main.py`**: ponto de composição. É o único que importa `config` e `adapters.*`; constrói um adapter de cada (um único cliente HTTP compartilhado) e os injeta nas rotas.

A herança nominal do port nos adapters e a regra "adapter importado só por `main`" estão justificadas no [ADR 0010](0010-leitura-da-metrica-de-acoplamento.md) (R1, R-b, R-c).

## Consequências

- **Melhor:**
  - **Feature testável sem rede.** Uma classe falsa de 3 linhas cumpre o `LanguageModel` e exercita as regras: 502 na classificação fora dos valores e no pedido `"123"`, 422 com `start > end`, e 301 tickets viram 3 chamadas `topics` em sequência. Foi assim que se conferiu que a refatoração não mudou essas regras.
  - **Uma troca, um arquivo.** Trocar gateway, SDK ou política de cliente mexe só em `adapters/gateway.py` e no construtor em `main`. Trocar modelo ou provider não mexe em nenhum arquivo da app.
  - **O port assíncrono prepara a próxima fase.** Streaming na F2 e lotes concorrentes na F3 não mudam a composição. O streaming vai pedir um método novo no port, e a mudança fica em port, adapter e F2.
  - **A métrica melhorou:**
    - zona de dor de {`config`, `llm`, `schemas`} para {`config`, `schemas`};
    - `ports` com A = 1, D = 0 e Ca = 6;
    - arestas de feature para concreto volátil: 9 → 0;
    - importadores de `config`: 6 → 1.
- **Pior / custo:**
  - Uma indireção (port + adapter + composição) para uma única implementação de cada port.
  - O D das features subiu de 0,20–0,25 para 0,33. Isso não é meta (R3).
  - As rotas viraram `async def`: qualquer chamada bloqueante numa feature para o event loop e, com ele, todas as requisições em andamento.
  - O port ainda não traduz erros: `openai.*Error` atravessa a feature até o FastAPI e vira 500, como na v1 (R5). Quando for preciso um 503 explícito, entra uma exceção do port, dentro do limite da R-d do ADR 0010.
- **Vigiar:**
  - Um segundo importador de adapter o põe na zona de dor (R1).
  - Toda feature nova segue a R-a e a R-b do ADR 0010.

## Evidência

Na árvore da tag `v2-decoupled`:

```bash
grep -rln openai app/helpdesk                                    # só app/helpdesk/adapters/gateway.py
grep -rlE "^\s*(from|import) .*(adapters|config)" app/helpdesk   # só app/helpdesk/main.py
grep -hE "^\s*(from|import) " app/helpdesk/{classification,extraction,suggestion,report}.py | sort -u
# → só fastapi, json, re, .ports e .schemas
grep -rn "CAPABILITY = " app/helpdesk                            # classify, extract, suggest, topics: um por feature
grep -n "AsyncOpenAI(\|max_tokens=" app/helpdesk/adapters/gateway.py   # max_retries=0 e timeout; max_tokens sempre
grep -rnE "num_retries|x-litellm|extra_headers|extra_body" app/helpdesk # só a docstring do adapter
git diff --stat v1-coupled -- tests/characterization/ && ./tests/run.sh characterization   # vazio; 20 passed in 51.98s
```

Linhas de `metrics/results/v1-coupled.csv` → `metrics/results/v2-decoupled.csv` (componente, Ca, Ce, I, A, D). Na v2, `extraction`, `report` e `suggestion` são iguais a `classification`.

```text
# v1                                # v2
llm,4,1,0.20,0.00,0.80              (removido)
config,6,0,0.00,0.00,1.00           config,1,0,0.00,0.00,1.00
classification,1,3,0.75,0.00,0.25   classification,1,2,0.67,0.00,0.33
                                    ports,6,0,0.00,1.00,0.00
                                    adapters.gateway,1,1,0.50,0.00,0.50
                                    adapters.tickets_file,1,1,0.50,0.00,0.50
```

Features com ports falsos, sem rede, dentro do container `app`:

```bash
docker compose exec -T app python - <<'EOF'
import asyncio
from datetime import date
from fastapi import HTTPException
from helpdesk import classification, extraction, report
from helpdesk.schemas import PeriodInput, TicketInput

class FakeModel:                       # cumpre ports.LanguageModel
    def __init__(self, answer): self.answer, self.calls = answer, []
    async def complete(self, capability, task, content): self.calls.append(capability); return self.answer

class FakeTickets:                     # cumpre ports.TicketSource
    async def in_period(self, start, end): return [{"id": f"TK-{i}", "text": "x"} for i in range(301)]

async def main():
    t = TicketInput(ticket_id="T1", text="oi")
    for coro in (classification.classify(t, FakeModel('{"category": "x", "priority": "high"}')),
                 extraction.extract(t, FakeModel('{"order_number": "123", "product": null}')),
                 report.topics(PeriodInput(start=date(2026, 8, 2), end=date(2026, 8, 1)), FakeModel(""), FakeTickets())):
        try: await coro
        except HTTPException as e: print(e.status_code, e.detail)
    m = FakeModel('{"topics": [{"topic": "a", "count": 1, "examples": []}]}')
    r = await report.topics(PeriodInput(start=date(2026, 8, 1), end=date(2026, 8, 31)), m, FakeTickets())
    print(len(m.calls), set(m.calls), r.total_tickets)

asyncio.run(main())
EOF
# → 502 Classificação fora dos valores permitidos
#   502 Número de pedido em formato inválido
#   422 A data inicial é posterior à data final
#   3 {'topics'} 301
```
