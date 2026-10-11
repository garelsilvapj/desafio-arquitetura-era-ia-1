# 0005. Cair primeiro para o large do outro provider e só aceitar o modelo fraco onde a feature tolera a resposta pior

- Status: aceita
- Data: 2026-10-11
- Nível: solução

## Resumo

| Feature | Fallback técnico | Modelo fraco | Sem nenhum large: resposta e status |
|---|---|---|---|
| F1 classificação | `claude-fake-large` | **aceito** (`gpt-fake-mini`) | 200, mesma classificação, via mini |
| F2 sugestão | `gpt-fake-large` | **aceito** (`claude-fake-mini`) | 200 em SSE, sugestão mais curta, via mini |
| F3 relatório | `gpt-fake-large` | **aceito** (`claude-fake-mini`) | job concluído (`done`), mesmos tópicos, via mini |
| F4 extração | `claude-fake-large` | **recusado** | `503` explícito, sem chamada ao mini |

## Contexto

A política corporativa exige modelo equivalente em dois providers para cada capacidade ([ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md)), e o [ADR 0003](0003-capacidades-logicas-e-mapeamento.md) deu a cada feature um grupo primário, um `-fallback` e, se houver, um `-weak`. Este ADR decide a ordem da cadeia e onde o modelo fraco é aceito. Na v1 não havia fallback: com a Anthropic em `error_500`, F2 e F3 responderam 500; com a OpenAI, F1 e F4.

O que se sabe dos modelos, medido direto no provider simulado (seção "Modelos mini × large" do `README.md`):

- **Large ↔ large é transparente.** `gpt-fake-large` e `claude-fake-large` deram saída idêntica em 400/400 chamadas (200 tickets × `classify`/`extract`), em 3 lotes de `topics` e em 5 tickets de `suggest`.
- **Mini ↔ large depende da tarefa.** Nos 5.000 tickets e nas amostras de `topics`/`suggest`:

| Tarefa | Mini comparado ao large |
|---|---|
| `classify` | idêntico em 5.000/5.000 (0 diferença em categoria e prioridade); ~0,4 s contra ~1,1 s |
| `topics` | idêntico nos 3 lotes comparados, nos 4 modelos (15 temas, soma 150, mesmos exemplos); ~2,7 s contra ~9,8 s por lote |
| `suggest` | diferente nos 5 tickets e sempre mais curto: ~1.070 contra ~2.430 caracteres (~267 contra ~605 tokens); mesma abertura e mesmo assunto; ~2,1 s contra ~16 s |
| `extract` | diferente em 1.533/5.000 (30,7%): `order_number` errado em 717 e `product` nulo em 953 |

O erro do mini na extração passa despercebido. No TK-00002 ("Tenho aqui a nota fiscal 530720. [...] pedido #605065"), o mini devolve `#530720`, a nota fiscal, e o large devolve `#605065`. No TK-00015, o mini devolve `#197527` (nota fiscal), e o large, `#972600`. Nos exemplos, o mini pega o primeiro número de 6 dígitos do texto. O resultado tem o formato válido (`^#\d{6}$`) e passa pela validação da app: é um dado errado com cara de certo.

**Esta é uma decisão de produto.** Aceitar uma resposta pior em troca de disponibilidade cabe a quem consome cada feature. Participam:

- o dono do atendimento (F1 e F2);
- o dono do painel de gestão (F3);
- o dono da automação de trocas e devoluções (F4).

A plataforma registra a decisão e a configura no gateway.

## Opções consideradas

1. **Sem fallback** (a v1). Um provider fora do ar derruba metade do helpdesk.
2. **Só o fallback técnico** (large ↔ large) em todas as features. Nunca piora a resposta, mas, com os dois large indisponíveis, F1, F2 e F3 falhariam mesmo tendo um mini que dá resposta igual ou útil.
3. **Técnico + fraco em todas, uniforme.** É simples, mas a F4 passaria a gravar o número de pedido errado, em silêncio, em ~14% dos tickets (717/5.000).
4. **Técnico em todas e fraco decidido por feature**, conforme o consumidor tolere ou não a resposta pior.

## Decisão

Opção 4. A cadeia é sempre **primário → large equivalente no outro provider → modelo fraco, se aceito**. O fraco só entra depois de os dois large falharem e recebe uma tentativa só ([ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md)).

### F1 classificação: aceita o mini

- **Consumidor:** o cliente que abriu o ticket e espera a confirmação (modo síncrono, [ADR 0004](0004-modo-de-execucao-por-feature.md)), e a fila de atendimento, roteada por categoria e prioridade.
- **O que piora:** nada observado. O mini classificou igual nos 5.000 tickets, e mais rápido.
- **Sem nenhum large:** `200` com a mesma classificação, e `/admin/calls` mostra `gpt-fake-mini`. Com os dois large em `error_500`, a F1 pela borda respondeu `200` em 4,61 s, `{"category": "delivery", "priority": "high"}`, depois de 2 + 2 + 1 chamadas (entre ~4,3 s e 5,1 s em outras execuções pela borda). Com os dois em `timeout`, respondeu em 8,43 s (1 + 1 + 1).

### F2 sugestão: aceita o mini

- **Consumidor:** o atendente, que lê, edita e só então envia. A sugestão é um rascunho.
- **O que piora:** o texto fica mais curto (~1.070 contra ~2.430 caracteres), com a mesma abertura e o mesmo assunto. Uma sugestão mais curta e no assunto é melhor que uma tela vazia.
- **Sem nenhum large:** o stream termina pelo mini (`200`, SSE). Com os dois large em `error_500`, o gateway abriu o stream e entregou o 1º trecho aos 4,96 s: 269 linhas `data:`, 1.066 caracteres e `[DONE]`, depois de 2 + 2 + 1 chamadas, a última em `claude-fake-mini`. Pela borda, na `main` (TK-00042): 1º trecho aos 4,90 s, 267 trechos (1.066 caracteres) e `event: end` aos 6,83 s.
- **Limites do streaming:**
  - O fallback só acontece antes do primeiro trecho. Erro depois de conteúdo enviado não é recuperado: com `midstream_error`, houve 1 chamada, 604 caracteres e a linha de erro, sem `[DONE]`. Pela borda, o atendente recebe os 151 trechos e depois `event: error` com `{"ticket_id": "TK-00042", "message": "A geração foi interrompida"}` ([ADR 0004](0004-modo-de-execucao-por-feature.md)).
  - O grupo `suggest-fallback` tem cadeia própria (`suggest-fallback: [suggest-weak]`). Quando o stream do fallback técnico falha antes do 1º trecho, o LiteLLM procura a cadeia pelo grupo que falhou, não pelo de origem. Sem essa linha, com os dois providers em `slow`, saiu `500` sem chamada ao mini.

### F3 relatório: aceita o mini

- **Consumidor:** o painel interno de gestão. Ninguém espera em tempo real: o relatório é um job ([ADR 0004](0004-modo-de-execucao-por-feature.md)).
- **O que piora:** nada observado nos lotes comparados, que deram os mesmos tópicos, contagens e exemplos.
- **Sem nenhum large:** o job termina (`done`) pelo mini. Com os dois large em `error_500`, um lote foi para `claude-fake-mini` em 7,41 s (2 + 2 + 1). Pela borda, na `main`, o job de 1 dia ficou pronto (`303`) aos 7,08 s, e o relatório trouxe o mesmo começo da linha de base: `"Problema no rastreio", count 20, examples TK-00001, TK-00007, TK-00008`.
- **Vigiar:** um relatório pode juntar lotes respondidos por modelos diferentes. Hoje isso não aparece no resultado, porque as saídas são iguais.

### F4 extração: recusa o mini

- **Consumidor:** a automação de troca e devolução, sem revisão humana.
- **O que piora:** um número de pedido errado dispara a troca do pedido errado, com frete, baixa de estoque e um cliente que não pediu nada.
- **Decisão:** não existe `extract-weak`, e a cadeia é só `extract: [extract-fallback]`. Sem nenhum large, a app responde `503` com `detail` explícito, e o mini não é chamado. Um erro explícito, que a automação pode reprocessar depois, é melhor que um dado errado com formato válido.
- **Observado no gateway**, com os dois large em `error_500`: 2 + 2 chamadas, nenhuma ao mini, e erro aos 5,12 s (`InternalServerError`; com os dois em `timeout`, `408` aos 8,05 s, 1 + 1). A tradução desse erro para `503` é da app.
- **Observado pela borda**, na `main`, no mesmo cenário: `503` aos 4,74 s, com `Retry-After: 10` e `{"detail": "Extração indisponível no momento: os modelos confiáveis para extração falharam e, por política, nenhum modelo menos confiável é usado. Encaminhe o ticket para tratamento manual."}`. `/admin/calls` mostra `gpt-fake-large` 500 ×2 e `claude-fake-large` 500 ×2, e nenhuma chamada a mini.

### Fallback técnico, em todas as features

Com o provider inteiro do primário em `error_500`, as quatro capacidades fizeram 2 chamadas ao primário e 1 com sucesso no large do outro provider, pelo gateway e pela borda, com e sem streaming. A resposta não muda, porque os large são equivalentes. Tempos pela borda, na `main` (entre parênteses, a medição anterior, com a F2 e a F3 ainda síncronas):

- F1: 3,04 s (2,84 s);
- F4: 3,70 s (3,80 s);
- F3 de 1 dia: job pronto aos 12,11 s (12,13 s);
- F2 em streaming: 1º trecho aos 3,30 s e fim aos 18,89 s (síncrona: 18,85 s).

### Onde mora

- **No gateway**, em `gateway/config.yaml`: os grupos `<cap>-fallback` e `<cap>-weak` (sem `extract-weak`) e `router_settings.fallbacks` (`classify: [classify-fallback, classify-weak]`, `suggest: [suggest-fallback, suggest-weak]`, `suggest-fallback: [suggest-weak]`, `topics: [topics-fallback, topics-weak]`, `extract: [extract-fallback]`).
- **Na app**: o que a falha da cadeia vira para quem consome. O adapter converte toda falha do gateway em `LanguageModelUnavailable`, e cada feature a traduz: `503` com `detail` na F1 e na F4, `503` ou `event: error` na F2, job `failed` na F3 ([ADR 0004](0004-modo-de-execucao-por-feature.md)). A app não escolhe modelo nem cadeia.

## Consequências

- **Melhor:**
  - A queda de um provider não derruba nenhuma feature.
  - A queda dos dois large (por modelo) só derruba a F4, e com erro explícito.
  - O fraco é mais barato, então cair nele não ameaça o teto do [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md).
- **Pior / custo:**
  - O fraco mora nos mesmos providers dos large (`gpt-fake-mini` na OpenAI, `claude-fake-mini` na Anthropic). Ele cobre a falha dos modelos large, não a dos providers. Com os dois providers inteiros em `error_500`, as quatro capacidades falharam em 3,9–5,2 s, depois de 2 + 2 + 1 chamadas (2 + 2 na F4). Pela borda, na `main`: F1 `503` aos 4,52 s, F2 `503` aos 4,16 s, job da F3 `failed` aos 4,05 s.
  - Cada salto soma tempo: no pior caso, 5 chamadas ao provider por requisição (4 na F4), com os tempos do [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md).
  - A resposta pior chega ao usuário sem aviso: o contrato das rotas é fixo e não diz qual modelo respondeu.
- **Vigiar:**
  - **Uso do fraco:** `x-litellm-model-group` terminando em `-weak` nas respostas do gateway, e `model_group` em `/spend/logs`. Fraco frequente é incidente nos large, não operação normal.
  - **Corpo de erro do gateway:** ele expõe à chave chamadora a lista de fallbacks e os nomes dos grupos internos. A app não repassa esse corpo ao cliente.
  - **Revisão:** a decisão é revista se um modelo mudar (troca no [ADR 0003](0003-capacidades-logicas-e-mapeamento.md)) ou se o consumidor de uma feature mudar, por exemplo, se a F4 ganhar revisão humana.

## Evidência

Cenários reproduzidos com o provider zerado antes de cada um, contando as tentativas em `/admin/calls` (não rodar com a stack em uso por outra pessoa: muda o modo do provider):

```bash
set -a; . ./.env; set +a
curl -s -X POST localhost:8090/admin/reset
# a) provider inteiro do primário fora:   -d '{"provider":"openai","mode":"error_500"}'
# b) os dois large fora (falha por modelo), dois comandos:
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"openai","model":"gpt-fake-large","mode":"error_500"}'
curl -s -X POST localhost:8090/admin/failures -H 'Content-Type: application/json' -d '{"provider":"anthropic","model":"claude-fake-large","mode":"error_500"}'
curl -s -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' \
  -d '{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'          # 200, via mini
curl -s -w ' %{http_code}\n' -X POST localhost:8000/tickets/extraction -H 'Content-Type: application/json' \
  -d '{"ticket_id":"TK-00002","text":"Tenho aqui a nota fiscal 530720. O produto veio quebrado na caixa, pedido #605065. Sou cliente da loja há bastante tempo. Fico no aguardo de uma solução."}'   # 503
curl -s "localhost:8090/admin/calls?last=10"
curl -s -X POST localhost:8090/admin/reset
```

Tentativas observadas (`/admin/calls`, ordem cronológica):

```text
a) openai error_500, classify:    gpt-fake-large 500, gpt-fake-large 500, claude-fake-large 200        (3,34 s no gateway)
a) anthropic error_500, suggest:  claude-fake-large 500 ×2, gpt-fake-large 200                         (18,63 s; stream: 1º trecho 3,11 s)
b) dois large, classify:  gpt-fake-large 500 ×2, claude-fake-large 500 ×2, gpt-fake-mini 200           (x-litellm-model-group: classify-weak)
b) dois large, topics:    claude-fake-large 500 ×2, gpt-fake-large 500 ×2, claude-fake-mini 200        (topics-weak, 7,41 s)
b) dois large, suggest:   claude-fake-large 500 ×2, gpt-fake-large 500 ×2, claude-fake-mini 200 (267 tokens de saída)
b) dois large, extract:   gpt-fake-large 500 ×2, claude-fake-large 500 ×2                             (nenhum mini; gateway 500 aos 5,12 s)
   corpo: "Received Model Group=extract\nAvailable Model Group Fallbacks=['extract-fallback']…"
```
