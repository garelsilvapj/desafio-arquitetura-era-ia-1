# Helpdesk com IA: da integração frágil à arquitetura resiliente

Este repositório é o fork de solução do desafio https://github.com/devfullcycle/desafio-arquitetura-era-ia-1 (o enunciado completo vive lá). Ele parte da aplicação herdada (`app/`) e a evolui, com decisões registradas e evidência medida, até uma arquitetura resiliente atrás de um AI Gateway.

As seções abaixo são completadas conforme a evolução avança. Por ora existe apenas o diagnóstico da v1.

Todos os comandos assumem a stack no ar (`docker compose up -d --build --wait`) e o avaliador sempre entrando pela borda (`localhost:8000`). Variáveis usadas nos exemplos:

```bash
T1='{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
T2='{"ticket_id":"TK-00002","text":"Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065. Sou cliente da loja há bastante tempo. Fico no aguardo de uma solução."}'
```

## Como rodar

### Subir a stack

```bash
cp .env.example .env && docker compose up -d --build --wait
curl -s localhost:8000/health   # {"status":"ok"}
```

Serviços: `edge` (nginx, `:8000`, a borda), `app` (o helpdesk), `gateway` (AI Gateway LiteLLM, `:4000`), `gateway-db` (Postgres do gateway: chaves virtuais e gasto) e `provider-fake` (`:8090`). A app fala só com o gateway, usando os nomes lógicos das capacidades (`classify`, `suggest`, `topics`, `extract`) e a chave virtual `GATEWAY_APP_KEY`. Modelo físico, provider e chaves dos providers ficam em `gateway/config.yaml` e no ambiente do serviço `gateway`.

### Testes de caracterização

```bash
./tests/run.sh characterization
```

Fixam o comportamento externo da v1 pela borda (corpos completos, não só status): classificação, sugestão de resposta, relatório de tópicos (um único dia, 137 tickets), extração e erros de validação (422). Os textos longos ficam em `tests/characterization/golden/`. Não testam provedores, modelos nem `/admin/calls`, então a mesma suíte deve passar sem mudanças contra a v2.

- Variáveis: `BASE_URL` (padrão `http://localhost:8000`) e `PROVIDER_URL` (padrão `http://localhost:8090`).
- Duração: cerca de 1 minuto (as sugestões levam ~16 s cada).
- Requer `python3` >= 3.11 no host; o script cria `.venv/` e instala `tests/requirements.txt` sozinho. Argumentos extras vão para o pytest (`./tests/run.sh characterization -k health`).

### Métrica de acoplamento

```bash
python3 metrics/coupling.py <path>/app/helpdesk --name <nome>   # ex.: python3 metrics/coupling.py app/helpdesk --name v2-decoupled
```

Calcula Ca, Ce, I, A e D por módulo de `app/helpdesk/` e lista a zona de dor (A < 0,5, I < 0,5, D ≥ 0,5). Grava `metrics/results/<nome>.csv` e `.svg` (`--out-dir` muda a pasta). Só biblioteca padrão; não importa nem executa a app.

Para medir uma tag com a régua atual, sem mexer na cópia de trabalho:

```bash
git worktree add /tmp/medir-v1 v1-coupled
python3 metrics/coupling.py /tmp/medir-v1/app/helpdesk --name v1-coupled --out-dir /tmp/medicao
git worktree remove /tmp/medir-v1
```

## Diagnóstico da v1

Todas as medições abaixo foram feitas contra a v1 (commit base `a1aedd7`) pela borda; os trechos de `/admin/calls` citados vêm de `GET localhost:8090/admin/calls`.

### Dor 1: o relatório mensal nunca chega

**Comando**

```bash
curl -s -X POST localhost:8090/admin/reset
curl -s -o /dev/null -w 'status=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  -X POST localhost:8000/reports/topics -H 'Content-Type: application/json' \
  -d '{"start":"2026-08-01","end":"2026-08-31"}'
# em seguida, nos minutos seguintes:
curl -s "localhost:8090/admin/calls?last=100"
```

**Observado**

- O cliente recebe `504` aos 30,02 s (`status=504 ttfb=30.018731 total=30.018798`), vindo do nginx.
- O mês tem 5.000 tickets = 34 lotes de 150, processados em sequência, ~9,7 s cada (média 9.746 ms). O laço terminou ~331,5 s depois do início.
- Em `/admin/calls`, o número de chamadas `task=topics` cresce depois do 504: 4 em t=31 s, 10 em t=90 s, 19 em t=180 s, 31 em t=300 s, 34 em t=400 s (todas `200`).
- Total: 34 chamadas, 222.114 tokens de entrada e 12.162 de saída = US$ 0,8488 (claude-fake-large, US$ 3/M entrada, US$ 15/M saída). Chamadas iniciadas depois do 504: 30, com 195.483 de entrada e 10.742 de saída = US$ 0,7476 (88% do custo) pagos por uma resposta que ninguém recebeu. Desistir não é cancelar.

**Causa no código**

- `app/helpdesk/report.py:15-22`: laço sequencial sobre os lotes, uma chamada bloqueante de cada vez, sem limite de tempo total, sem paralelismo e sem detecção de que o cliente desistiu.
- `app/helpdesk/main.py:30-32`: a rota devolve o relatório inteiro na mesma requisição HTTP (síncrono por contrato); a borda corta em 30 s (`edge/nginx.conf`, `proxy_read_timeout 30s`) mas o app continua trabalhando.
- `app/helpdesk/config.py:18`: `TICKETS_PER_CALL = 150`, fixo.

### Dor 2: o atendente olha uma tela parada

**Comando**

```bash
curl -s -X POST localhost:8090/admin/reset
curl -sN -w '\nstatus=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  -X POST localhost:8000/tickets/reply-suggestion -H 'Content-Type: application/json' -d "$T2"
curl -s "localhost:8090/admin/calls?last=3"
```

**Observado**

- `status=200 ttfb=16.135914 total=16.135979`: nenhum byte antes do fim (ttfb ≈ total). Com TK-00042: 15,96 s.
- `/admin/calls`: `task=suggest`, `stream=false`, `output_tokens=613`, `duration_ms=16127`. A saída longa (~600 tokens a 40 tokens/s no large) só é entregue quando termina.
- Para comparação, as outras features respondem em ~1,1 s (classificação e extração) e o relatório de um dia (137 tickets, 1 lote) em ~9,8 s.

**Causa no código**

- `app/helpdesk/llm.py:20-26`: `anthropic_client.messages.create(...)` sem `stream=True`; o texto só existe quando a chamada inteira termina.
- `app/helpdesk/suggestion.py:6-7` e `app/helpdesk/main.py:20-22`: a rota devolve um `ReplySuggestion` completo (JSON), sem resposta incremental.

### Dor 3: um provider fora do ar derruba parte do helpdesk

**Comando**

```bash
# (i) Anthropic caída
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"anthropic","mode":"error_500"}'
for r in "tickets/classification $T1" "tickets/extraction $T2" "tickets/reply-suggestion $T1"; do
  set -- $r; curl -s -o /dev/null -w "$1 status=%{http_code} total=%{time_total}\n" -X POST localhost:8000/$1 -H 'Content-Type: application/json' -d "$2"
done
curl -s -o /dev/null -w 'reports/topics status=%{http_code} total=%{time_total}\n' -X POST localhost:8000/reports/topics -H 'Content-Type: application/json' -d '{"start":"2026-08-01","end":"2026-08-31"}'
curl -s "localhost:8090/admin/calls?last=30"

# (ii) OpenAI caída: mesmo roteiro, com
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"openai","mode":"error_500"}'

# (iii) Anthropic pendurada (timeout)
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"anthropic","mode":"timeout"}'
curl -s -o /dev/null -w 'status=%{http_code} total=%{time_total}\n' -X POST localhost:8000/tickets/reply-suggestion -H 'Content-Type: application/json' -d "$T1"
curl -s "localhost:8090/admin/calls?last=5"   # repetir aos ~35 s, ~310 s e ~330 s
```

**Observado**

| Cenário | F1 classificação | F4 extração | F2 sugestão | F3 relatório |
|---|---|---|---|---|
| (i) anthropic `error_500` | 200 (1,09 s) | 200 (1,09 s) | **500** (1,36 s) | **500** (1,36 s) |
| (ii) openai `error_500` | **500** (1,25 s) | **500** (1,46 s) | 200 (15,96 s) | 200 (9,83 s, 1 dia) |

- Corpo do erro: `Internal Server Error` (500 sem corpo útil; nenhuma mensagem para o atendente, nenhum fallback).
- Tentativas por requisição que falha: 3 em `/admin/calls` (1 original + 2 retentativas automáticas do SDK, com backoff curto de ~0,4 s e ~1 s). No F3 o laço morre no primeiro lote.
- (iii) `timeout`: F2 termina em `504` aos 30,03 s (borda) mas a chamada continua aberta no provider: aos 35 s, 120 s e 290 s `status=504 duration_ms=null`. Aos ~300 s o provider devolve o 504, o SDK trata como 5xx e abre uma **segunda tentativa** (visível às 00:10:02, 300 s depois da primeira), de novo pendurada. A thread do app fica bloqueada até ~900 s (3 tentativas × 300 s) por requisição.

**Causa no código**

- `app/helpdesk/config.py:10-13`: cada feature está atada a um único modelo/provider (classificação e extração na OpenAI, sugestão e relatório na Anthropic); `app/helpdesk/classification.py:11`, `extraction.py:12`, `suggestion.py:6`, `report.py:18` chamam o provider diretamente, sem alternativa.
- `app/helpdesk/llm.py:8-9`: clientes criados sem `timeout` nem `max_retries`, valendo os padrões do SDK (ver abaixo).
- `app/helpdesk/main.py:15-32`: rotas `def` síncronas, executadas no pool de threads do FastAPI; chamadas penduradas ocupam threads, e sem limite próprio de tempo a capacidade do app encolhe.

### Dor 4: trocar um modelo exige alterar código e reconstruir a imagem

**Comando**

```bash
curl -s -X POST localhost:8090/admin/reset
sed -i 's/^CLASSIFICATION_MODEL = .*/CLASSIFICATION_MODEL = "gpt-fake-mini"/' app/helpdesk/config.py
docker compose restart app
curl -s -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' -d "$T1"
curl -s "localhost:8090/admin/calls?last=1"      # ainda gpt-fake-large
docker compose up -d --build app
curl -s -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' -d "$T1"
curl -s "localhost:8090/admin/calls?last=1"      # agora gpt-fake-mini
git checkout -- app/helpdesk/config.py && docker compose up -d --build app   # reverter
```

**Observado**

- Antes: `gpt-fake-large`, 1,09 s. Após editar o arquivo e só `restart`: continua `gpt-fake-large` (1,10 s). Após `up -d --build`: `gpt-fake-mini`, 0,41 s.
- A mudança de um modelo exigiu editar fonte e reconstruir a imagem.

**Causa no código**

- `app/helpdesk/config.py:10-13`: nomes de modelo como constantes no código; `config.py:3-7`: chaves e URLs dos providers lidas no app (`FAKE_*_KEY` via `env_file: .env`, `compose.yaml:20`) e URLs fixas no fonte.
- `app/Dockerfile:6`: `COPY helpdesk ./helpdesk` assa o código na imagem; `compose.yaml:19` usa `build: ./app` sem volume para o código, então `restart` reaproveita a imagem antiga.
- Cada serviço do app conhece diretamente os dois SDKs (`app/helpdesk/llm.py:3-4`), sem camada de roteamento.

### Padrões dos SDKs

Medidos dentro do container `app` (`docker compose exec app python -c ...`, imprimindo `openai._constants` / `anthropic._constants` e os clientes de `helpdesk.llm`):

| SDK | Versão | Timeout padrão | `max_retries` |
|---|---|---|---|
| openai | 3.19.0 | connect 5 s, read/write/pool **600 s** | **2** |
| anthropic | 1.8.0 | connect 5 s, read/write/pool **600 s** | **2** |

Os clientes de `llm.py:8-9` herdam esses valores. A borda desiste em 30 s: o SDK espera até 20× mais, e ainda tenta de novo em 5xx/429/timeout. Resultado: o cliente vê 504 enquanto o app segue ocupado e gastando.

### Modelos mini × large

Chamadas direto ao provider (`localhost:8090`). Varredura dos 5.000 tickets de `data/tickets.jsonl` em `classify` e `extract` (large × mini, `gpt-fake-*`, concorrência 100, 0 erros), mais `topics` (3 lotes de 150) e `suggest` (5 tickets) nos dois providers.

| Tarefa | Resultado mini vs large | Latência / custo |
|---|---|---|
| classify | **5.000/5.000 idênticos** (0 diferenças em categoria e prioridade) | ~0,4 s vs ~1,1 s; US$ 0,15/0,60 vs 2,50/10,00 por 1M |
| extract | 3.467 idênticos, **1.533 diferentes (30,7%)**: 717 `order_number` divergentes, 953 `product` divergentes | idem |
| topics (3 lotes) | idêntico nos 3 lotes (15 temas, soma 150); mini = large | ~2,7 s vs ~9,8 s por lote |
| suggest (5 tickets) | texto diferente nos 5 tickets, mini mais curto (~1.070 caracteres / ~267 tokens vs ~2.430 / ~605); mesmo padrão nos 5 | ~2,1 s vs ~16 s |

- Falha do mini em `extract`: devolve o número da **nota fiscal** em vez do pedido (717 casos) e `product: null` onde o large acha o produto (953 casos). Exemplos: TK-00002 large `{"order_number":"#605065","product":null}`, mini `{"order_number":"#530720",...}` (a nota fiscal); TK-00015 large `#972600`, mini `#197527` (nota fiscal); TK-00007 large `product: "notebook 14 polegadas"`, mini `product: null`; TK-00030 large `cortina blackout`, mini `null`.
- `gpt-fake-large` e `claude-fake-large` produziram saída idêntica em todos os 400 testes (200 tickets × classify/extract), e também em topics (3 lotes) e suggest (5 tickets). O mesmo vale para os dois minis entre si em topics e suggest. Logo, large ↔ large é um fallback transparente entre providers; mini ↔ large não é equivalente em `extract` (e muda o texto em `suggest`), mas é equivalente em `classify` e, nos lotes testados, em `topics`.
