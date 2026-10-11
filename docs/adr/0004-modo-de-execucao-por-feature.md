# 0004. Executar cada feature no modo que o contrato pede: F1 e F4 síncronas, F2 em streaming (SSE) e F3 assíncrona (Asynchronous Request-Reply)

- Status: aceita
- Data: 2026-10-11
- Nível: solução

## Resumo

| Feature | Quem espera | Saída e latência no large | Modo | Efeito medido pela borda |
|---|---|---|---|---|
| F1 classificação | o cliente que abriu o ticket e a fila de atendimento | ~11 tokens, ~1,1 s | **síncrono** | 200 em 1,11 s; com o primário pendurado, 200 em 5,12 s |
| F2 sugestão de resposta | o atendente, olhando a tela | ~600 tokens, ~16 s; 1º token em ~0,8 s | **streaming (SSE)** | 1º trecho em 0,77–0,83 s; fim em ~16,4 s |
| F3 relatório de tópicos | ninguém em tempo real: o painel interno de gestão | mês: 34 lotes de ~9,8 s = ~330 s em sequência | **Asynchronous Request-Reply** | `202` em ~0,04 s; mês pronto em ~89 s (4 lotes em paralelo) |
| F4 extração de pedido | a automação de troca e devolução, sem revisão humana | ~11 tokens, ~1,1 s | **síncrono** | 200 em 1,09 s; sem os large, `503` explícito em 4,7 s |

Nenhum serviço novo no compose: o job da F3 roda dentro do processo da app.

## Contexto

Na v1 (e na v2, que não mudou comportamento), as quatro rotas eram síncronas: a resposta inteira saía na mesma requisição HTTP. A borda (`edge/nginx.conf`, que não pode ser alterado) encerra com `504` qualquer upstream que fique 30 s sem enviar bytes. O diagnóstico no `README.md` mostrou o efeito:

- **Dor 1: o relatório mensal nunca chega.** 34 lotes de 150 tickets, em sequência, ~9,7 s cada: o laço terminou ~331 s depois do início, e o cliente recebeu `504` aos 30,02 s. Das 34 chamadas, 30 começaram depois do 504 e custaram US$ 0,7476 (88% do total) por uma resposta que ninguém recebeu.
- **Dor 2: o atendente olha uma tela parada.** `ttfb=16.135914 total=16.135979`: nenhum byte antes do fim. A saída longa (~600 tokens a 40 tokens/s) só era entregue inteira.

Critérios do desafio que este ADR precisa cumprir, todos verificados pela borda em `tests/acceptance/`:

- F1 completa em até 3 s em operação normal e, com o primário pendurado, sucesso pelo fallback ou erro explícito em até 15 s;
- F2 com o 1º trecho rápido e o texto continuando a chegar; interrupção depois de conteúdo vira evento de erro no formato do contrato;
- F3 do mês sem `504`; se não houver como terminar, o job vira `failed` em menos de 60 s;
- nenhuma resposta `504` em nenhum cenário.

Para cada feature, a escolha seguiu três perguntas, nesta ordem:

1. **Alguém está esperando agora?** Se não, o trabalho sai da requisição (assíncrono).
2. **Cabe no orçamento de latência?** Se a resposta inteira, no pior caso, cabe no prazo de quem espera (e nos 30 s da borda), síncrono.
3. **É consumível aos poucos?** Se não cabe, mas quem espera aproveita o resultado parcial, streaming; se não aproveita, assíncrono.

Os prazos citados abaixo são os da app (`DEADLINE_SECONDS`, `FIRST_CHUNK_SECONDS`, `NEXT_CHUNK_SECONDS`, `BATCH_SECONDS` em cada feature). Timeouts, retry e o orçamento de tempo de ponta a ponta estão no [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md); a cadeia de fallback, no [ADR 0005](0005-politica-de-fallback.md).

## Opções consideradas

Modos disponíveis para cada feature:

1. **Síncrono**: a resposta inteira na mesma requisição.
2. **Streaming (SSE)**: a resposta começa a sair assim que existe o 1º trecho e continua na mesma conexão.
3. **Asynchronous Request-Reply**: `202` com `Location`; o cliente consulta o estado e busca o resultado quando ficar pronto.
4. **Assíncrono com callback** (webhook): o servidor avisa o cliente quando terminar.

As opções avaliadas para cada feature estão na seção dela.

## Decisão

### F1 classificação: síncrono

**Características do contrato.** O cliente que abriu o ticket espera a confirmação, e a fila de atendimento é roteada por categoria e prioridade. A saída é um JSON de dois campos (~11 tokens), ~1,1 s no large e ~0,4 s no mini. Só serve inteira: metade de um JSON não roteia nada.

**Árvore de decisão.**

- *Alguém está esperando agora?* Sim, o cliente e a fila.
- *Cabe no orçamento de latência?* Sim. Normal: 1,11 s, contra 3 s do critério. Pior caso medido no gateway: 12,06 s (os dois providers pendurados, 4 + 4 + 4 s), abaixo do prazo de 13 s da app, dos 15 s do critério e dos 30 s da borda.
- *É consumível aos poucos?* Não se aplica: cabe no orçamento.

**Opções.** Síncrono (escolhida). Streaming entregaria um JSON de dois campos em pedaços, sem uso. Assíncrono obrigaria o cliente a consultar um estado para receber, em ~1 s, uma resposta que já cabe na requisição.

**Contrato implementado.** `POST /tickets/classification` com `{"ticket_id", "text"}`:

- `200 {"ticket_id", "category", "priority"}`, igual à v1;
- `503` com `Retry-After: 10` e `{"detail": "Classificação indisponível no momento: nenhum modelo de linguagem conseguiu responder. Tente de novo em instantes."}`, quando o gateway esgota a cadeia ou quando o prazo total de 13 s estoura (`asyncio.wait_for`);
- `502 "Classificação fora dos valores permitidos"` e os `422` de validação, iguais à v1.

**Efeito medido** (pela borda):

- normal: `200` em 1,11 s;
- OpenAI (primário) pendurada: `200` em 5,12 s (o gateway corta aos 4 s e o `claude-fake-large` responde);
- os dois large em `error_500`: `200` em 4,75 s, via `gpt-fake-mini`;
- os dois providers pendurados: `503` em 12,08 s; em `error_500`: `503` em 4,52 s.

### F2 sugestão de resposta: streaming (SSE)

**Características do contrato.** O atendente espera na tela, lê a sugestão, edita e só então envia. A saída é longa: ~600 tokens, ~16 s no large, mas o 1º token sai em ~0,8 s. Cada frase já é útil quando chega.

**Árvore de decisão.**

- *Alguém está esperando agora?* Sim, o atendente.
- *Cabe no orçamento de latência?* Não. A resposta inteira leva 16 s em operação normal (tela parada) e 18,85 s com o fallback técnico; com o provider em `slow` (×10), passaria dos 30 s da borda.
- *É consumível aos poucos?* Sim: o atendente lê à medida que o texto chega.

**Opções.**

1. Síncrono (a v1): 16 s de tela parada, e `504` se a geração passar de 30 s.
2. **Streaming SSE** (escolhida).
3. Assíncrono (job + consulta): o atendente continuaria vendo nada até o fim (~16 s), a menos que a consulta devolvesse texto parcial, o que seria um streaming feito com polling, com mais requisições e estado de job na app.
4. WebSocket: canal bidirecional sem uso aqui (o pedido é um só), e a borda precisaria repassar o upgrade de protocolo.

**Por que streaming, e não assíncrono.** O consumidor lê enquanto o texto chega. O streaming não encurta a geração (continua ~16 s), mas muda a espera percebida: o 1º trecho aparece em ~0,8 s, em vez de 16 s. O assíncrono só faz sentido quando ninguém está olhando. O preço é uma conexão aberta por sugestão durante ~16 s, que passa pela borda sem problema, porque o limite dela é de 30 s **sem bytes**, e os trechos chegam a cada ~25 ms.

**Contrato implementado.** `POST /tickets/reply-suggestion` com `{"ticket_id", "text"}`:

- `200` com `Content-Type: text/event-stream; charset=utf-8` e `Cache-Control: no-cache`. A app também envia `X-Accel-Buffering: no`, que o nginx consome e não repassa ao cliente (ver o achado sobre a borda, abaixo).
- Eventos, com JSON em UTF-8:
  - cada trecho, assim que chega do gateway: `data: {"chunk": "<texto>"}`;
  - fim: `event: end` + `data: {"ticket_id": "<id>"}`;
  - falha depois do `200`: `event: error` + `data: {"ticket_id": "<id>", "message": "A geração foi interrompida"}`, e o stream fecha.
- **O `200` só sai depois do 1º trecho** (prazo de 20 s). Falha antes de qualquer conteúdo vira `503` JSON comum, com `Retry-After: 10` e `{"detail": "Sugestão de resposta indisponível no momento: nenhum modelo de linguagem conseguiu responder. Tente de novo em instantes."}`. Assim, o cliente distingue "não começou" (status HTTP) de "começou e quebrou" (`event: error`).
- Entre trechos, rede de segurança de 10 s: depois disso, `event: error`. O gateway já corta antes, em 5 s (`stream_timeout`).
- A concatenação dos `chunk` é o texto da v1, byte a byte (goldens da caracterização). Os `422` não mudaram.

**Efeito medido** (pela borda):

- normal: 1º trecho em 0,77–0,83 s, fim em 16,4–16,6 s (três medições, 606–613 linhas `data:`); os trechos chegam um a um, a cada ~25 ms;
- Anthropic (primário) em `error_500`: 1º trecho em 3,30 s pelo `gpt-fake-large`, fim em 18,89 s;
- Anthropic pendurada: 1º trecho em 5,85 s (o gateway corta o primário aos 5,02 s);
- `midstream_error` no primário: 151 trechos (604 caracteres) e depois `event: error`, aos 4,70 s. Sem fallback, porque já havia conteúdo entregue ([ADR 0005](0005-politica-de-fallback.md));
- os dois large em `error_500`: 1º trecho em 4,90 s pelo `claude-fake-mini` (267 trechos, 1.066 caracteres);
- os dois providers em `error_500`: `503` em 4,16 s; pendurados: `503` em 15,09 s; em `slow`: 1º trecho em 13,05 s pelo mini;
- cliente que desiste aos 3 s: a chamada ao provider termina junto (`duration_ms=3021`). Com streaming, a desistência atravessa borda, app e gateway.

### F3 relatório de tópicos: Asynchronous Request-Reply

**Características do contrato.** O consumidor é o painel interno de gestão: alguém pede o relatório de um período e volta para ver depois. O painel não tem endpoint para receber callback. O mês tem 5.000 tickets, que não cabem numa chamada (contexto de 8.000 tokens): são 34 lotes de 150 (R8 do [plano](../refactoring-plan.md)), ~9,8 s cada no large. Os temas são somados entre lotes: a contagem e os exemplos de cada tema só ficam certos com todos os lotes.

**Árvore de decisão.**

- *Alguém está esperando agora?* Não em tempo real: o painel consulta quando quiser.
- *Cabe no orçamento de latência?* Não. Em sequência, ~330 s; com 4 lotes em paralelo, ~89 s. Os dois passam dos 30 s da borda.
- *É consumível aos poucos?* Não. Um relatório parcial tem contagens erradas e ordem errada dos temas, e o painel quer o relatório inteiro.

**Opções.**

1. Síncrono (a v1): `504` aos 30 s, e o trabalho seguia pago depois (88% do custo).
2. Streaming: manteria a conexão aberta por 90–330 s, com bytes de manutenção para fugir do corte da borda, e entregaria parciais que ninguém consome. Uma queda de conexão perderia o trabalho inteiro.
3. **Asynchronous Request-Reply** (escolhida).
4. Callback: o painel não tem endpoint para recebê-lo.
5. Síncrono, só com paralelismo: com 4 lotes em paralelo, ~89 s, ainda acima dos 30 s; com os 34 de uma vez, ~10 s em operação normal, mas qualquer fallback (um lote leva 12–33 s nesses casos) voltaria a estourar a borda, e o pico seria de 34 chamadas simultâneas por relatório.

**Por que assíncrono, e não streaming.** Ninguém consome resultado parcial, e o trabalho dura mais que a paciência da borda. O assíncrono separa a vida da requisição da vida do trabalho: o `POST` responde em ~0,04 s, e uma falha vira `failed` com motivo, em vez de `504`.

**Contrato implementado.**

- `POST /reports/topics` com `{"start", "end"}`:
  - valida na hora, com os mesmos `422` da v1 (`start > end` → `{"detail": "A data inicial é posterior à data final"}`; data inválida → erro do pydantic);
  - lê os tickets do período (~10 ms), cria o job e responde `202 Accepted` com `Location: /reports/topics/status/<id>` e `Retry-After: 5`;
  - corpo: `{"job_id": "<id>", "state": "pending", "progress": "0/<tickets do período>"}`.
- `GET /reports/topics/status/<id>`:
  - em andamento: `200 {"job_id", "state": "pending" | "running", "progress": "<n>/<total>"}` e `Retry-After: 5`, onde `n` conta os tickets dos lotes já concluídos;
  - falhou: `200 {"job_id", "state": "failed", "progress", "reason": "<motivo legível>"}`, sem `Retry-After`;
  - pronto: `303 See Other` com `Location: /reports/topics/<id>`;
  - id desconhecido ou já descartado: `404`.
- `GET /reports/topics/<id>`:
  - pronto: `200` com o mesmo corpo da v1 (`start`, `end`, `total_tickets`, `topics`);
  - ainda gerando: `409` com `Location` e `Retry-After`, e `{"detail": "O relatório ainda está sendo gerado (<n>/<total> tickets); consulte /reports/topics/status/<id>"}`;
  - falhou: `409 {"detail": "O relatório falhou: <motivo>"}`;
  - desconhecido: `404`.
- O `Location` é relativo de propósito: a borda repassa `Host` sem a porta, e uma URL absoluta montada pela app sairia `http://localhost/...`.
- Período sem tickets: o job termina na hora, e a 1ª consulta já dá `303`.

**Execução do job** (`app/helpdesk/report.py` e `app/helpdesk/jobs.py`):

- Lotes de 150, até 4 ao mesmo tempo (`TOPICS_CONCURRENT_BATCHES`). O número 4 é um compromisso, não um ótimo medido: leva o mês de ~330 s para ~89 s e limita a 4 as chamadas simultâneas de um relatório ao gateway. Também limita o que se paga numa falha: com os dois providers fora, 4 lotes entram na cadeia de fallback, e não 34.
- As respostas são juntadas na **ordem original dos lotes**, então o relatório é igual ao do processamento sequencial. Isso foi verificado com as 34 saídas reais de agosto reproduzidas no `report.py` da tag `v2-decoupled` e no atual, e com a ordem de conclusão embaralhada (5 rodadas).
- Prazo de 50 s por lote, acima do pior caso do gateway (45,07 s, 15 + 15 + 15 s). Se um lote falha ou estoura o prazo, os outros são cancelados (os que esperavam vaga nunca chegam ao gateway), e o job vira `failed` com o motivo (`Lote 4 de 34 falhou: capacidade topics indisponível no gateway (HTTP 500)`).
- Todo caminho termina em `done` ou `failed`: um job nunca fica preso em `running`. Uma falha inesperada vira `failed` com "Falha inesperada ao gerar o relatório (detalhes no log da app)".

**Efeito medido** (pela borda):

- um dia (137 tickets, 1 lote): `202` em 0,029 s, `303` aos 10,11 s, corpo igual ao golden da v1;
- mês: `202` em 0,037 s, `303` aos 88,65 s (34 chamadas `topics`, todas `200`); progresso de ~600 em ~600 tickets a cada ~10 s;
- Anthropic (primário) em `error_500`, um dia: pronto em 12,11 s pelo `gpt-fake-large`;
- os dois large pendurados, um dia: pronto em 33,27 s pelo `claude-fake-mini`;
- os dois providers em `error_500`, mês: `failed` aos 4,05 s, com o motivo, e o resultado responde `409`. Chegaram ao provider 4 lotes (20 chamadas); os outros 30 nunca foram chamados;
- os dois providers pendurados, mês: `failed` aos 45,33 s, abaixo dos 60 s do critério.

### F4 extração de pedido: síncrono

**Características do contrato.** O consumidor é a automação de troca e devolução, sem revisão humana. Ela espera o número do pedido para seguir. A saída é um JSON de dois campos (~11 tokens), ~1,1 s.

**Árvore de decisão.**

- *Alguém está esperando agora?* Sim, a automação, que só continua com o resultado.
- *Cabe no orçamento de latência?* Sim. Normal: 1,09 s. Pior caso medido no gateway: 10,07 s (os dois providers em `429`), abaixo do prazo de 13 s da app e dos 30 s da borda.
- *É consumível aos poucos?* Não se aplica: cabe no orçamento.

**Opções.** Síncrono (escolhida). Assíncrono obrigaria a automação a consultar um estado para um resultado de ~1 s. Streaming não tem uso para um JSON de dois campos.

**Contrato implementado.** `POST /tickets/extraction` com `{"ticket_id", "text"}`:

- `200 {"ticket_id", "order_number", "product"}`, igual à v1;
- `503` com `Retry-After: 10` e `{"detail": "Extração indisponível no momento: os modelos confiáveis para extração falharam e, por política, nenhum modelo menos confiável é usado. Encaminhe o ticket para tratamento manual."}`, quando os dois large falham (não há modelo fraco para `extract`, [ADR 0005](0005-politica-de-fallback.md)) ou o prazo de 13 s estoura;
- `502 "Número de pedido em formato inválido"` e os `422`, iguais à v1.

**Efeito medido** (pela borda):

- normal: `200` em 1,09 s;
- OpenAI (primário) em `error_500`: `200` em 3,70 s, `#605065`, pelo `claude-fake-large`;
- os dois large em `error_500`: `503` em 4,74 s, depois de 2 + 2 chamadas e nenhuma ao mini;
- os dois providers em `429`: `503` em 9,76 s.

### O custo do assíncrono, e por que não há fila, worker nem banco

O job da F3 é uma tarefa `asyncio` no processo da app (`JobRegistry` em `app/helpdesk/jobs.py`). Isso tem custos aceitos:

- **Estado só na memória.** Um restart da app perde os jobs: os em andamento param, os prontos somem, e a consulta passa a responder `404`. O cliente pede de novo. Sobreviver a restart está fora do escopo deste desafio.
- **Um processo só.** O `CMD` da imagem sobe um processo uvicorn. Com `--workers N` ou mais de uma réplica, a consulta poderia cair num processo que não conhece o job (`404`). Escalar a app exige estado compartilhado (banco ou fila), e isso pede um ADR novo nessa hora.
- **Registro limitado.** Ficam os 100 jobs mais recentes (`TOPICS_JOBS_KEPT`), e os terminados mais antigos saem primeiro. Verificado: com 105 jobs criados, os 5 primeiros dão `404`.
- **Sem cancelamento pelo cliente.** O job corre até `done` ou `failed`.
- **A desistência não para o que já está no gateway.** Sem streaming, o LiteLLM não interrompe a cadeia de retry e fallback quando a app cancela um lote. Com os dois providers em `error_500`, 3 chamadas ao mini, dos lotes que já estavam no gateway, chegaram ao provider logo depois do `failed`. O que a app garante é que nenhum lote novo começa depois da falha ([ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md)).

**Por que nenhum serviço novo.** O requisito é responder `202` e permitir a consulta, nunca dar `504` e falhar em menos de 60 s. Ele cabe no processo: o trabalho é esperar o gateway (I/O), e o event loop da app já faz isso para as outras rotas. Uma fila (Redis/RQ, Celery) ou um banco de jobs seria serviço novo no compose e, pela regra do desafio, precisaria de um ADR que o justificasse por um requisito. Nenhum requisito pede durabilidade dos jobs ou mais de um processo. O único serviço além dos do desafio continua sendo o `gateway-db` ([ADR 0008](0008-banco-do-gateway.md)).

### Achado sobre a borda: `X-Accel-Buffering: no`

O nginx bufferiza a resposta do upstream por padrão (`proxy_buffering on`), e o arquivo da borda não pode ser alterado. Por isso a F2 envia `X-Accel-Buffering: no`, o jeito de o upstream desligar esse buffer por resposta. O nginx consome o header, que não chega ao cliente. A expectativa, ao implementar, era que sem ele os trechos chegassem em blocos.

A medição não confirmou essa expectativa. Com uma cópia exata de `edge/nginx.conf` (só o upstream trocado) num `nginx:1.27-alpine` descartável, na frente de duas cópias da app, uma com o header e outra sem, a F2 real (TK-00042, gateway e provider normais) chegou igual nas duas:

| Variante | Eventos `data:` | 1º | Último | Maior intervalo entre eventos |
|---|---|---|---|---|
| com `X-Accel-Buffering: no` | 607 | 0,80 s | 16,39 s | 0,04 s |
| sem o header | 607 | 0,80 s | 16,39 s | 0,05 s |

Com esta borda e este ritmo (um trecho a cada ~25 ms), o nginx repassa cada trecho quando ele chega, e o header não é o que faz o streaming funcionar. Ele fica como defesa barata: garante, por resposta, que a borda não acumule a sugestão, sem depender de uma configuração que a app não controla. Quem prova o streaming pela borda é o teste `test_f2_first_chunk_fast_and_text_keeps_arriving`.

## Consequências

- **Melhor:**
  - Nenhuma rota depende mais de terminar em 30 s: as síncronas têm prazo de 13 s, a F2 manda o 1º byte em até 20 s e um trecho a cada ~25 ms, e a F3 responde em ~0,04 s. Nenhum `504` em todos os cenários medidos.
  - O atendente vê texto em ~0,8 s, em vez de 16 s.
  - O relatório do mês chega (~89 s, contra nunca), e uma falha vira `failed` com motivo em 4–45 s.
  - Cada falha tem um formato próprio: `503` com `Retry-After` antes de qualquer conteúdo, `event: error` depois dele, `failed` + `409` no job.
- **Pior / custo:**
  - O contrato da F2 e o da F3 mudaram. Os clientes precisam ler SSE e fazer a consulta do job. A suíte de caracterização foi atualizada só nesses dois pontos, contra os mesmos goldens.
  - Uma conexão aberta por sugestão durante ~16 s.
  - Jobs na memória de um processo (seção acima).
  - Erro depois do 1º trecho não tem fallback nem status HTTP: o atendente recebe o texto parcial e o `event: error`.
- **Vigiar:**
  - Cliente da F3 que não segue o `Retry-After` gera consultas desnecessárias (são baratas: só leem a memória).
  - Se a app passar a ter mais de um processo, o registro de jobs precisa sair da memória.
  - Lotes de origens diferentes (large e mini) podem compor o mesmo relatório ([ADR 0005](0005-politica-de-fallback.md)).

## Evidência

Com a stack no ar e o provider em modo normal (`curl -s -X POST localhost:8090/admin/reset`):

```bash
T1='{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
T2='{"ticket_id":"TK-00002","text":"Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065. Sou cliente da loja há bastante tempo. Fico no aguardo de uma solução."}'
# F1 e F4: síncronas
curl -s -w '\nstatus=%{http_code} total=%{time_total}\n' -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' -d "$T1"
curl -s -w '\nstatus=%{http_code} total=%{time_total}\n' -X POST localhost:8000/tickets/extraction -H 'Content-Type: application/json' -d "$T2"
# F2: streaming
curl -sN -i -X POST localhost:8000/tickets/reply-suggestion -H 'Content-Type: application/json' -d "$T2"
# F3: 202, consulta, 303, resultado
curl -s -i -X POST localhost:8000/reports/topics -H 'Content-Type: application/json' -d '{"start":"2026-08-01","end":"2026-08-31"}'
curl -s -i localhost:8000/reports/topics/status/<job_id>     # 200 running "n/5000" ... depois 303
curl -s -L localhost:8000/reports/topics/status/<job_id>     # segue o 303 até o relatório
```

Saídas observadas (2026-10-11, pela borda):

```text
{"ticket_id":"TK-00042","category":"delivery","priority":"high"}        status=200 total=1.108315
{"ticket_id":"TK-00002","order_number":"#605065","product":null}        status=200 total=1.098070

HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
cache-control: no-cache

data: {"chunk": "Olá!"}
data: {"chunk": " Lam"}
...
data: {"chunk": "to."}
event: end
data: {"ticket_id": "TK-00002"}

HTTP/1.1 202 Accepted
location: /reports/topics/status/d51f97ff07284738b1223c7cca384003
retry-after: 5
{"job_id":"d51f97ff07284738b1223c7cca384003","state":"pending","progress":"0/137"}

0s  200 {"job_id":"640d7fcd...","state":"running","progress":"0/5000"}
10s 200 {"job_id":"640d7fcd...","state":"running","progress":"600/5000"}
...
85s 200 {"job_id":"640d7fcd...","state":"running","progress":"4800/5000"}
90s 303 (location: /reports/topics/640d7fcd...)
{"start":"2026-08-01","end":"2026-08-31","total_tickets":5000,"topics":[{"topic":"Atraso na entrega","count":570,...

GET /reports/topics/<id> antes de pronto → 409, location + retry-after, {"detail":"O relatório ainda está sendo gerado (0/137 tickets); consulte /reports/topics/status/<id>"}
GET /reports/topics/status/nao-existe    → 404 {"detail":"Relatório não encontrado (id desconhecido ou já descartado)"}
```

F4 sem nenhum large (os dois large em `error_500`; zerar o provider depois):

```bash
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"openai","model":"gpt-fake-large","mode":"error_500"}'
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"anthropic","model":"claude-fake-large","mode":"error_500"}'
curl -s -i -X POST localhost:8000/tickets/extraction -H 'Content-Type: application/json' -d "$T2"
curl -s -X POST localhost:8090/admin/reset
```

```text
HTTP/1.1 503 Service Unavailable
retry-after: 10
{"detail":"Extração indisponível no momento: os modelos confiáveis para extração falharam e, por política, nenhum modelo menos confiável é usado. Encaminhe o ticket para tratamento manual."}
status=503 total=4.871546   (/admin/calls: extract gpt-fake-large 500 ×2, claude-fake-large 500 ×2, nenhum mini)
```

Testes de aceitação (`./tests/run.sh acceptance`, pela borda, com falhas provocadas no provider-fake e um hook que reprova qualquer `504`):

| Teste | O que prova |
|---|---|
| `test_f1_classification_complete_within_3s` | F1 síncrona completa em ≤ 3 s |
| `test_f1_primary_hung_answers_within_15s` | F1 com o primário pendurado: `200` ou `503` em ≤ 15 s |
| `test_f2_first_chunk_fast_and_text_keeps_arriving` | `text/event-stream`, 1º trecho em ≤ 1,5 s e antes da metade do total, > 100 trechos espalhados até o `event: end` |
| `test_f2_interrupted_after_content_sends_error_event` | `midstream_error`: trechos e depois `event: error` com `{"ticket_id", "message": "A geração foi interrompida"}` |
| `test_f2_failure_before_any_content_is_plain_503` | falha antes do 1º trecho: `503` JSON, nenhum evento |
| `test_f3_month_is_accepted_at_once_and_finishes_in_background` | mês: `202` em ≤ 1 s com `Location` e `Retry-After`; estado `200` com `n/5000`; resultado `409` antes de pronto; `303`; 5.000 tickets; 34 chamadas `topics` |
| `test_f3_unknown_job_is_404` | `404` no estado e no resultado de id desconhecido |
| `test_f3_job_fails_with_reason_within_60s_when_both_providers_down` | `failed` com `reason` em ≤ 60 s; resultado `409` com o motivo; nenhum lote novo depois do `failed` |
| `test_both_large_down_f4_explicit_503_and_no_mini` | F4: `503` com "tratamento manual", nenhuma chamada a mini |

A suíte de caracterização (`./tests/run.sh characterization`, 20 testes) confere que o texto concatenado da F2 e o relatório da F3 continuam iguais aos goldens da v1. Na última execução: `20 passed in 53.07s`.
