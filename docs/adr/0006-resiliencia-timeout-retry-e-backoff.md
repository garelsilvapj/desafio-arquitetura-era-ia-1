# 0006. Repetir e cortar só no gateway, por deployment e por tipo de erro, e dar à app apenas prazos totais por feature

- Status: aceita
- Data: 2026-10-11
- Nível: solução

## Contexto

Na v1, a política de chamada era a dos SDKs, herdada sem escolha (diagnóstico no `README.md`):

- **Timeout de leitura de 600 s e 2 retentativas** (`openai` 3.19.0 e `anthropic` 1.8.0). Cada requisição que falhava gerava 3 chamadas em `/admin/calls`, com backoff curto de ~0,4 s e ~1 s.
- **Provider pendurado prendia a app.** Com a Anthropic em `timeout`, o atendente recebeu `504` da borda aos 30 s, a chamada seguiu aberta no provider, o SDK abriu uma segunda tentativa aos 300 s, e a thread ficou presa até ~900 s (3 × 300 s).

Ao pôr o gateway no caminho ([ADR 0002](0002-ai-gateway-self-hosted.md)), a v2 desligou o retry do SDK (`max_retries=0`), mas o problema mudou de lugar:

- **O LiteLLM também repete por padrão** (R6 do [plano](../refactoring-plan.md)). Com a OpenAI em `error_500`, uma chamada da app virou 3 no provider, e o `500` saiu em 4,23 s.
- **A rede de segurança da app cortava a F2.** Com 25 s no cliente e a Anthropic pendurada, a F2 síncrona respondeu `500` aos 25,0 s, e a chamada de fallback seguiu no gateway até ~36 s, paga e descartada (R10).

O que as features precisam está nos critérios do desafio e no [ADR 0004](0004-modo-de-execucao-por-feature.md):

- F1 responde em até 15 s com o primário pendurado;
- a F2 manda o 1º trecho rápido e não fica 30 s sem bytes;
- um job da F3 que não tem como terminar vira `failed` em menos de 60 s;
- nenhuma resposta é o `504` da borda (30 s sem bytes).

A cadeia de fallback (primário → large do outro provider → fraco, se aceito) é do [ADR 0005](0005-politica-de-fallback.md). Este ADR decide quantas vezes cada destino é tentado, quanto tempo cada tentativa pode esperar e onde cada limite mora.

## Opções consideradas

1. **Retry e timeout no SDK da app** (a v1). Cada aplicação repete a política, e o gateway, que repete por padrão, multiplica as tentativas.
2. **Retry nos dois lugares** (app e gateway). As tentativas se multiplicam: 3 na app × 3 no gateway = até 9 chamadas ao mesmo destino, antes do fallback.
3. **Padrões do LiteLLM** (`num_retries` 2, sem timeout por deployment, cooldown ligado). O `500` sai em ~4,2 s, o provider pendurado espera o timeout padrão, e o cooldown tira o deployment de circulação depois de falhas.
4. **Política explícita no gateway, por deployment e por tipo de erro, sem retry na app, que só impõe prazos totais por feature.**

## Decisão

Opção 4. Quem repete e quem corta cada tentativa é o gateway. A app não repete nada e só garante o prazo total de cada feature, por cima de tudo.

### Onde mora cada mecanismo

| Mecanismo | Onde | Valor | Por quê | Evidência |
|---|---|---|---|---|
| Timeout por tentativa de `classify` e `extract` | `gateway/config.yaml`, `timeout` dos deployments `classify*` e `extract*` | 4 s | ~3,5× o normal (~1,1 s). Um provider pendurado custa 4 s, e não os 300 s do modo `timeout` | OpenAI pendurada: `gpt-fake-large` cortado aos 4,02 s; F1 `200` aos 5,12 s pela borda |
| Timeout com streaming de `suggest` | `stream_timeout` dos 3 deployments `suggest*` | 5 s por espera (até o 1º trecho e entre trechos) | O 1º trecho sai em ~0,8 s e os seguintes a cada ~25 ms. Com `stream=true`, o LiteLLM usa o `stream_timeout` (`_get_timeout` em `router.py`), e a app sempre chama `suggest` com streaming | Anthropic pendurada: corte aos 5,02 s e 1º trecho aos 5,85 s; os dois providers pendurados: `503` aos 15,09 s (5 + 5 + 5) |
| Timeout sem streaming de `suggest` | `timeout` dos 3 deployments `suggest*` | 20 s | Só vale para chamada **sem** streaming, que a app não faz mais (diagnóstico com curl, por exemplo). Sem streaming, a resposta inteira leva ~16 s no large. Com 5 s, toda chamada assim estouraria no primário e no técnico e cairia em silêncio no fraco | Primário pendurado, sem streaming, no gateway: `200` em 35,99 s (corte aos 20,01 s + 16 s do fallback) |
| Timeout de `topics` | `timeout` dos 3 deployments `topics*` | 15 s | Um lote leva ~9,8 s no large (~50% de folga). O pior caso de um lote (15 + 15 + 15 = 45 s) fica abaixo do prazo de 50 s do lote e dos 60 s do critério | Anthropic pendurada: corte aos 15,01 s; dia pronto em 25,18 s |
| Retry de erro transitório | `router_settings.retry_policy`: `InternalServerErrorRetries`, `ServiceUnavailableErrorRetries`, `RateLimitErrorRetries` | 1 retentativa (500, 503, 429) | Logo atrás há um fallback em outro provider: insistir mais no mesmo só atrasa a resposta | `error_500` no primário: 2 chamadas nele + 1 no fallback, nas quatro capacidades |
| Retry de timeout | `retry_policy.TimeoutErrorRetries` | 0 | Quem não respondeu em N s dificilmente responde na segunda vez, e repetir dobraria o pior caso (`classify`: 4 → 8 s só no primário) | Primário pendurado: 1 + 1 |
| Retry de erro do pedido | `BadRequestErrorRetries`, `AuthenticationErrorRetries`, `ContentPolicyViolationErrorRetries` | 0 | O mesmo pedido dá o mesmo erro | — |
| Retry dos demais tipos | `retry_policy.DefaultRetries` e `num_retries: 0` | 0 | Falha de conexão e outros tipos vão direto ao fallback. Nada cai num padrão global (R6). Quando uma política se aplica, o LiteLLM pula a checagem própria de "vale repetir?", por isso todos os tipos estão escritos | `num_retries` explícito; nenhuma contagem acima da tabela abaixo |
| Retry do modelo fraco | `router_settings.model_group_retry_policy` de `classify-weak`, `suggest-weak` e `topics-weak` | 0 em todos os tipos | Última opção: uma tentativa só, para o pior caso caber no prazo da app | Dois large em `error_500`: 2 + 2 + 1 |
| Backoff entre tentativas | Código do LiteLLM (sem configuração) | 0,5 s × 2^n + até 0,75 s de jitter; em `429`, o `Retry-After` do provider + jitter | Padrão aceito: com 1 retentativa, a espera é curta e respeita o provider em `429` | `500`: 0,53–1,10 s entre as tentativas; `429` com `Retry-After: 2`: 2,02–2,75 s |
| Fallback | `router_settings.fallbacks` | primário → `<cap>-fallback` → `<cap>-weak` (sem `extract-weak`) | [ADR 0005](0005-politica-de-fallback.md) | `/admin/calls` |
| Cooldown | `router_settings.disable_cooldowns: true` | desligado | Com um deployment por grupo, o cooldown faria a requisição seguinte pular o primário sem tentar, e a contagem de tentativas deixaria de ser previsível. Circuit breaker fica fora do escopo | Mesmas contagens (2 + 1, 2 + 2 + 1) em todas as repetições dos cenários, inclusive na suíte de aceitação, que os roda em sequência |
| Retry na app | `AsyncOpenAI(..., max_retries=0)` em `app/helpdesk/adapters/gateway.py` | 0 | Um só lugar repete. Na v1, os padrões do SDK davam 3 tentativas por requisição; somadas às do gateway, seriam até 9 por destino | R10: 3 chamadas por requisição antes e depois do gateway, e não 9 |
| Prazo total da F1 e da F4 | `DEADLINE_SECONDS` em `classification.py` e `extraction.py` (`asyncio.wait_for`) | 13 s | Acima do pior caso medido no gateway (12,06 s na F1, 10,07 s na F4) e abaixo dos 15 s do critério e dos 30 s da borda. Cobre também o pior caso teórico (~15 s, abaixo) | F1 com os dois providers pendurados: `503` aos 12,08 s |
| Prazo da F2 | `FIRST_CHUNK_SECONDS` e `NEXT_CHUNK_SECONDS` em `suggestion.py` | 20 s até o 1º trecho; 10 s entre trechos | Acima do pior caso até o 1º trecho (15,08 s) e abaixo dos 30 s sem bytes da borda. Entre trechos, o gateway já corta em 5 s | Dois providers pendurados: `503` aos 15,09 s; dois em `slow`: 1º trecho aos 13,05 s pelo mini |
| Prazo do lote da F3 | `BATCH_SECONDS` em `report.py` | 50 s | Acima do pior caso de um lote (45,07 s) e abaixo dos 60 s do critério | Mês com os dois providers pendurados: `failed` aos 45,33 s |
| Timeout do socket do cliente | `GATEWAY_TIMEOUT_SECONDS` em `app/helpdesk/config.py` | 55 s | Só rede de segurança, acima do maior prazo de feature (50 s). Os 25 s da v2 cortariam um lote legítimo que cai no mini (33 s) | Nunca atuou: todo prazo de feature é menor |

A app nunca envia política ao gateway: nada de `timeout`/`num_retries` no corpo nem headers `x-litellm-*` ([ADR 0009](0009-portas-e-adaptadores-na-aplicacao.md)). O `timeout` do construtor do SDK é local ao cliente.

### Número máximo de tentativas por destino (observado)

Contagem em `GET localhost:8090/admin/calls` por requisição da app, com o provider zerado antes de cada cenário. "—" = destino que não existe, por decisão do [ADR 0005](0005-politica-de-fallback.md).

| Capacidade | Erro | Primário | Fallback técnico | Fraco |
|---|---|---|---|---|
| `classify`, `suggest`, `topics` | `500` ou `429` | 2 | 2 | 1 |
| `classify`, `suggest`, `topics` | timeout (pendurado ou lento sem bytes) | 1 | 1 | 1 |
| `extract` | `500` ou `429` | 2 | 2 | — |
| `extract` | timeout | 1 | 1 | — |
| `suggest` | erro no meio do stream, depois de conteúdo entregue | 1 | 0 | 0 |

- Máximo de chamadas ao provider por requisição: 5 (2 + 2 + 1); 4 na `extract`.
- Erro no meio do stream: com o técnico falhando depois de conteúdo (primário em `error_500`, técnico em `midstream_error`), foram 2 + 1, e o fraco não foi chamado. Depois de conteúdo entregue, não há fallback.

### Orçamento de tempo de ponta a ponta

| Feature | Prazo da app | Pior caso do gateway (medido) | Observado pela borda no pior caso | Borda (30 s sem bytes) | Critério |
|---|---|---|---|---|---|
| F1 | 13 s no total | 12,06 s (dois providers pendurados: 4 + 4 + 4 s); 10,67 s (dois large em `429`, `200` pelo mini) | `503` aos 12,08 s | resposta inteira em ≤ 13 s | ≤ 15 s com o primário pendurado: `200` aos 5,12 s |
| F4 | 13 s no total | 10,07 s (dois providers em `429`) | `503` aos 9,76 s | ≤ 13 s | erro explícito em vez de dado errado |
| F2 | 20 s até o 1º trecho; 10 s entre trechos | 1º trecho: 15,08 s (três `stream_timeout` de 5 s); 13,19 s com sucesso no mini (`slow`). Entre trechos, o gateway corta em 5 s | `503` aos 15,09 s; 1º trecho aos 13,05 s (dois em `slow`) | 1º byte em ≤ 20 s; depois, um trecho a cada ~25 ms e nunca mais de 10 s sem trecho | 1º trecho rápido: 0,8 s em operação normal |
| F3 | 50 s por lote; a falha de um lote cancela os outros | 45,07 s por lote (15 + 15 + 15 s); 32,74 s com sucesso no mini | `failed` aos 45,33 s (mês, dois providers pendurados); 4,05 s (dois em `500`) | fora do caminho: o `POST` responde `202` em ~0,04 s | `failed` em < 60 s |
| Socket do cliente | 55 s | — | nunca atuou | — | — |

Limite teórico, não observado: na F1, primário e técnico em `429` com o jitter máximo (2 × 2,75 s em cada) e o fraco pendurado (4 s) somariam ~15 s. Esse caso é cortado pelo prazo de 13 s da app, com `503`, ainda dentro do critério. É para isso que existe o prazo total: o timeout do gateway vale por tentativa, não pela requisição.

### Limitações do LiteLLM v1.102.1 e como foram tratadas

| Limitação (onde, na fonte dentro da imagem) | Efeito medido | Tratamento |
|---|---|---|
| Dorme também depois da **última** retentativa, antes de devolver o erro ou seguir para o fallback (`router.py`, `async_function_with_retries`) | +1,0–1,7 s por grupo em `500`; +2,0–2,8 s em `429` | Uma retentativa só; nenhuma em timeout; o tempo está somado na tabela de orçamento |
| Sem streaming, a chamada ao provider **não é cancelada** quando o cliente desiste | v2: F2 síncrona cortada pela app aos 25 s, e o fallback seguiu até ~36 s, pago. Na main: lotes da F3 já no gateway completam a cadeia depois do `failed` (+3 chamadas ao mini) | Prazos da app acima do pior caso do gateway, para que a app quase nunca desista antes dele; a F2 usa streaming, e aí a desistência chega ao provider (cliente desiste aos 3 s → `duration_ms=3021`); a F3 cancela os lotes que ainda não começaram e limita a 4 os que estão em andamento |
| O corpo de erro expõe à chave chamadora a cadeia de fallback e os grupos internos (`Received Model Group=extract\nAvailable Model Group Fallbacks=['extract-fallback']…`) | — | O adapter registra o erro no log e levanta `LanguageModelUnavailable("capacidade extract indisponível no gateway (HTTP 500)")`. As rotas respondem com a própria mensagem (`503` com `detail`, `event: error`, `reason` do job), nunca com o corpo do gateway |
| Deployment inválido é descartado em silêncio na subida (`ignore_invalid_deployments=True` em `proxy_server.py`) | Com `opnai/` digitado, a F1 passou a ser atendida, sem aviso, pelo `classify-fallback` | `gateway/provision_keys.py` compara os `model_name` do `config.yaml` com os deployments carregados e deixa o gateway `unhealthy` se faltar algum ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md)) |
| Sem política declarada, o roteador repete 2 vezes (R6) | 3 chamadas e `500` em 4,23 s | `num_retries: 0` e todos os tipos escritos em `retry_policy` |
| No streaming, a cadeia é procurada pelo grupo que falhou, não pelo de origem | Sem a linha `suggest-fallback: [suggest-weak]`, com os dois providers em `slow`, saiu `500` sem chamada ao mini | Linha acrescentada em `fallbacks` ([ADR 0005](0005-politica-de-fallback.md)) |
| O header `x-litellm-attempted-retries` reflete só o último grupo da cadeia | — | Tentativas são contadas em `/admin/calls` (ou em `/spend/logs`), não nesse header |

## Consequências

- **Melhor:**
  - O número de chamadas por requisição é conhecido e limitado (no máximo 5; 4 na `extract`), e um provider pendurado custa 4, 5 ou 15 s, conforme a capacidade, em vez de 300–900 s.
  - Cada feature tem um prazo total que garante o seu critério, mesmo nas combinações não medidas.
  - Mudar a política é editar `gateway/config.yaml` e reiniciar o gateway; a app não muda.
- **Pior / custo:**
  - Sem cooldown nem circuit breaker, durante uma queda longa do primário, toda requisição paga as tentativas nele antes do fallback: a F1 sai em ~3 s com o primário em `500` e em ~5 s com ele pendurado, em vez de ~1,1 s.
  - Uma retentativa só pode desistir cedo demais de uma falha transitória mais longa. Nesse caso, quem responde é o fallback.
  - A política vale para o grupo, não por chave: outra aplicação no mesmo gateway herda os mesmos timeouts.
- **Vigiar:**
  - Uso do fallback técnico e do fraco (`x-litellm-model-group` terminando em `-fallback`/`-weak`, `model_group` em `/spend/logs`). Fallback frequente indica primário degradado e é sinal para avaliar um circuit breaker.
  - Ao trocar o modelo de uma capacidade ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md)), revisar o timeout dela: um modelo mais lento que o atual estouraria os 4 s ou os 15 s.
  - Ao atualizar a versão do LiteLLM, refazer as contagens da tabela de tentativas: os comportamentos acima foram lidos na fonte da v1.102.1 e confirmados em `/admin/calls`.

## Evidência

Configuração em vigor:

```bash
grep -hE "^\s+(timeout|stream_timeout):" gateway/config.yaml | sort | uniq -c
# → 3 stream_timeout: 5 (suggest*) | 3 timeout: 15 (topics*) | 3 timeout: 20 (suggest*) | 5 timeout: 4 (classify*, extract*)
sed -n '/^router_settings:/,/^general_settings:/p' gateway/config.yaml | grep -vE "^\s*#"
grep -n "max_retries" app/helpdesk/adapters/gateway.py                                  # max_retries=0
grep -nE "DEADLINE_SECONDS =|FIRST_CHUNK_SECONDS =|NEXT_CHUNK_SECONDS =|BATCH_SECONDS =" app/helpdesk/*.py
grep -n "GATEWAY_TIMEOUT_SECONDS =" app/helpdesk/config.py                              # 55.0
grep -rnE "num_retries|x-litellm|extra_headers|extra_body" app/helpdesk                # só a docstring do adapter
```

Contagem de tentativas, com o provider zerado antes de cada cenário (não rodar com a stack em uso por outra pessoa):

```bash
T1='{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
curl -s -X POST localhost:8090/admin/reset
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"openai","mode":"timeout"}'
curl -s -w '\nstatus=%{http_code} total=%{time_total}\n' -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' -d "$T1"
curl -s "localhost:8090/admin/calls?last=10"
curl -s -X POST localhost:8090/admin/reset
```

Saídas observadas (`/admin/calls`, `t` = segundos desde a 1ª chamada ao provider):

```text
openai timeout, F1:           200 em 5.12 s     0.00 gpt-fake-large 504 (pendurada, cortada aos 4 s) | 4.02 claude-fake-large 200
openai error_500, F1:         200 em 3.04 s     0.00 gpt-fake-large 500 | 0.53 gpt-fake-large 500 | 1.93 claude-fake-large 200
dois large error_500, F1:     200 em 4.75 s     gpt-fake-large 500 ×2 | claude-fake-large 500 ×2 | 4.35 gpt-fake-mini 200
dois providers timeout, F1:   503 em 12.08 s    0.00 gpt-fake-large 504 | 4.02 claude-fake-large 504 | 8.03 gpt-fake-mini 504
dois providers timeout, F2:   503 em 15.09 s    0.00 claude-fake-large 504 | 5.01 gpt-fake-large 504 | 10.04 claude-fake-mini 504
dois providers 429, F4:       503 em 9.76 s     0.00 gpt-fake-large 429 | 2.12 gpt-fake-large 429 | 4.38 claude-fake-large 429 | 6.99 claude-fake-large 429
dois providers timeout, F3:   failed em 45.33 s 4 lotes × (claude-fake-large 504 | 15.04 gpt-fake-large 504 | 30.07 claude-fake-mini 504); 30 lotes nunca chamados
```

Os testes de aceitação `test_f1_primary_hung_answers_within_15s`, `test_f{1,2,3,4}_primary_down_falls_back_to_other_large` (no máximo 2 tentativas no primário, depois o large do outro provider) e `test_f3_job_fails_with_reason_within_60s_when_both_providers_down` verificam esses limites pela borda (`./tests/run.sh acceptance`).
