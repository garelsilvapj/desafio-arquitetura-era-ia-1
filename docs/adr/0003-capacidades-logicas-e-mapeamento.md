# 0003. Expor à app só quatro capacidades lógicas, uma por feature, e mapear o modelo físico no gateway

- Status: aceita
- Data: 2026-10-11
- Nível: solução

## Contexto

O [ADR 0002](0002-ai-gateway-self-hosted.md) põe o gateway entre a app e os providers. Falta decidir o vocabulário entre os dois: que nome a app manda no campo `model` e onde esse nome vira modelo físico e provider. Na v1 (diagnóstico no `README.md`):

- **O modelo físico estava no código.** `config.py` tinha um nome físico por feature (`CLASSIFICATION_MODEL = "gpt-fake-large"` etc.), e o provider aparecia no nome da função (`call_openai`/`call_anthropic`). F1 e F4 usavam o `gpt-fake-large`; F2 e F3, o `claude-fake-large`.
- **Trocar de modelo era deploy (Dor 4).** Com a F1 apontada para o `gpt-fake-mini` em `config.py`, o `restart` manteve o `gpt-fake-large` (1,10 s). Só `up -d --build` trocou o modelo (0,41 s).

As quatro features precisam de coisas diferentes do modelo, e isso pesou na escolha dos nomes:

| Feature | Saída e latência no large | O que a capacidade precisa |
|---|---|---|
| F1 classificação | ~11 tokens, ~1,1 s | timeout curto; o mini dá a mesma resposta (5.000/5.000) |
| F2 sugestão | ~600 tokens, ~16 s; 1º trecho em ~0,8 s com streaming | timeout por trecho, não pela resposta inteira |
| F3 relatório | lote de 150 tickets: ~6,5 mil tokens de entrada (máx. 6.729 em agosto), ~9,8 s | contexto ≥ 8.000 tokens |
| F4 extração | ~11 tokens, ~1,1 s | timeout curto; o mini troca o número do pedido em 717 de 5.000 tickets |

Os dois large são intercambiáveis: deram a mesma saída em 400/400 chamadas de `classify`/`extract`, em 3 lotes de `topics` e em 5 de `suggest`. O provider do primário não muda a resposta.

## Opções consideradas

1. **Nome físico na app**, como na v1, só que pelo gateway (`model: "gpt-fake-large"`). Trocar de modelo continua sendo deploy da app. Com a chave da app, o gateway já recusa nome físico com `403` ([ADR 0002](0002-ai-gateway-self-hosted.md)).
2. **Nome por provider** (`openai-large`, `anthropic-large`). O provider volta para a app, como em `call_openai`, e o nome mente quando o fallback leva a chamada para o outro provider.
3. **Nome por porte** (`large`, `small`). A app escolhe a qualidade, e todas as features que usam `large` dividem um timeout, uma cadeia de fallback e uma política de modelo fraco. Não dá para dar 4 s à F1 e 20 s à F2, nem para a F1 aceitar o mini e a F4 recusar. Levar só a F1 para o mini exigiria mudar a app (de `large` para `small`); mudar o `large` levaria as quatro features.
4. **Uma capacidade por feature** (`classify`, `suggest`, `topics`, `extract`), com o modelo físico decidido no gateway.

## Decisão

Opção 4. A app conhece quatro nomes, um por feature, declarados como constante na própria feature (`CAPABILITY`, [ADR 0009](0009-portas-e-adaptadores-na-aplicacao.md)). Em `gateway/config.yaml`, cada nome é um grupo com um deployment, e dois grupos internos por capacidade formam a cadeia de fallback ([ADR 0005](0005-politica-de-fallback.md)):

| Capacidade (feature) | Primário (`<cap>`) | Fallback técnico (`<cap>-fallback`) | Modelo fraco (`<cap>-weak`) | Timeout ([ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md)) |
|---|---|---|---|---|
| `classify` (F1) | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | `openai/gpt-fake-mini` | 4 s |
| `suggest` (F2) | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` | 20 s; 5 s por trecho |
| `topics` (F3) | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` | 15 s |
| `extract` (F4) | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | nenhum (não existe `extract-weak`) | 4 s |

- **Os grupos internos não são chamáveis pela app.** A chave `helpdesk-app` só tem os quatro nomes na lista de modelos ([ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md)). Chamar `classify-weak` direto com ela dá `403 key_model_access_denied`. O fallback interno do gateway os usa mesmo assim, porque `enforce_fallback_model_access` vem desligado.
- **Cada deployment está escrito por extenso**, sem âncoras nem merge do YAML, para que trocar um modelo seja uma edição local, sem efeito em outro grupo. Cada deployment declara o próprio preço (ADR 0007).

**Por que o large é o primário nas quatro.** A v2 é uma refatoração sem mudança de comportamento: os primários repetem o mapeamento da v1 (F1 e F4 no `gpt-fake-large`, F2 e F3 no `claude-fake-large`), e a suíte de caracterização passa inalterada. A conta do teto do [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md) também parte dos large. Manter a divisão da v1 entre os providers não muda a resposta, porque os large são equivalentes, e reparte a carga dos primários entre os dois providers.

**Considerada e não adotada agora: o mini como primário de `classify` e `topics`.** A saída foi idêntica (5.000/5.000 classificações; 3/3 lotes de tópicos, com a mesma contagem de tokens), e o mini é mais rápido e mais barato:

| Capacidade | Latência large → mini | Custo por chamada large → mini (`x-litellm-response-cost`) | No mês (tokens do ADR 0001) |
|---|---|---|---|
| `classify` | ~1,1 s → ~0,4 s | US$ 0,0001575 → 0,00000945 (16,7× menos) | US$ 1,18 → 0,07 |
| `topics` | ~9,8 s → ~2,7 s por lote | US$ 0,0238 → 0,0063 por lote de 137 tickets (3,75× menos) | US$ 3,40 → 0,91 (4 relatórios) |

A economia é de US$ 3,59 por mês (US$ 51,97 → 48,38, ~7%), pequena porque a F2 responde por US$ 46,27 do total. Não foi adotado agora porque:

- a v2 não muda comportamento;
- a equivalência foi observada no provider simulado e, em `topics`, em só 3 lotes;
- a qualidade aceitável é decisão do dono do produto (ADR 0005), não da plataforma.

Adotar é só configuração: é a troca descrita abaixo, já executada com `classify → gpt-fake-mini`. Ao adotar, a cadeia precisa ser revista (o large passa a fallback).

## Consequências

- **Melhor:**
  - **Trocar o modelo de uma capacidade é editar o `gateway/config.yaml` e reiniciar o gateway.** O procedimento:
    1. editar só a linha `model:` do deployment e as duas de preço;
    2. rodar `docker compose restart gateway` e esperar `healthy` (~11–12 s);
    3. conferir o modelo físico com a master key (`/model/info`);
    4. a próxima chamada já mostra o modelo novo em `/admin/calls`.

    A app não é reconstruída nem reiniciada (mesmo `StartedAt`, mesma imagem).
  - A app não cita nenhum nome físico (`grep` vazio em `app/`).
  - Timeout, cadeia de fallback e política de modelo fraco são escolhidos por feature, sem afetar as outras.
- **Pior / custo:**
  - São 11 deployments para manter, com preço em cada um, e repetição deliberada no YAML.
  - Feature nova exige uma capacidade nova: deployments, entrada na chave da app em `gateway/provision_keys.py` e uma decisão de modelo fraco (ADR 0005).
  - Nesta instalação de uma réplica, a troca tira o gateway do ar por ~11–12 s. Em produção, com réplicas, ela seria gradual (fora deste repositório).
- **Passa a ser necessário: proteção contra erro de digitação.** O LiteLLM descarta em silêncio um deployment inválido na subida (`ignore_invalid_deployments`). Com `model: opnai/gpt-fake-mini`:
  - o log mostrou `Error creating deployment ... LLM Provider NOT provided`;
  - o `/v1/models` da chave da app continuou listando `classify`;
  - a F1 passou a ser atendida, sem aviso, pelo `classify-fallback` (Anthropic).

  Por isso `gateway/provision_keys.py` compara os `model_name` do `config.yaml` com o `/v1/models` da master key e, se faltar algum, não cria `/tmp/keys.ok`. O gateway fica `unhealthy`, com a causa no log.
- **Vigiar:**
  - A checagem só pega deployment que o LiteLLM recusou na subida. Um nome de modelo bem formado que o provider não conhece seria carregado e falharia só na chamada, caindo no fallback. Esse caso não foi medido.
  - Todo modelo posto em `topics` precisa de contexto ≥ 8.000 tokens.
  - O `/v1/models` da chave da app repete a lista de modelos da chave. Para ver o modelo físico de cada grupo, use `/model/info` com a master key.

## Evidência

```bash
set -a; . ./.env; set +a
grep -rnE "gpt-fake|claude-fake|FAKE_|-weak|-fallback" app/   # vazio
grep -rn "CAPABILITY = " app/helpdesk                         # classify, extract, suggest, topics
curl -s localhost:4000/v1/models -H "Authorization: Bearer $GATEWAY_APP_KEY"   # ids: classify, extract, suggest, topics
curl -s localhost:4000/model/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -c 'import json, sys
for m in sorted(json.load(sys.stdin)["data"], key=lambda m: m["model_name"]): print(m["model_name"], "->", m["litellm_params"]["model"])'
# 11 linhas: classify -> openai/gpt-fake-large, classify-fallback -> anthropic/claude-fake-large, classify-weak -> openai/gpt-fake-mini, ...
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $GATEWAY_APP_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"classify-weak","max_tokens":50,"messages":[{"role":"user","content":"TASK: classify\noi"}]}'
# → 403 key_model_access_denied: "This key can only access models=['classify', 'suggest', 'topics', 'extract']. Tried to access classify-weak"
```

Troca da `classify` para o mini, e de volta (só o deployment `classify`; os outros grupos não casam com `classify$`):

```bash
docker inspect -f '{{.State.StartedAt}} {{.Image}}' $(docker compose ps -q app)   # anotar
sed -i '/- model_name: classify$/,/timeout:/{s|model: openai/gpt-fake-large|model: openai/gpt-fake-mini|;s|input_cost_per_token: 0.0000025$|input_cost_per_token: 0.00000015|;s|output_cost_per_token: 0.00001$|output_cost_per_token: 0.0000006|}' gateway/config.yaml
docker compose restart gateway && docker compose ps gateway                      # healthy
curl -s -X POST localhost:8000/tickets/classification -H 'Content-Type: application/json' \
  -d '{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
curl -s "localhost:8090/admin/calls?last=1"                                      # model: gpt-fake-mini
docker inspect -f '{{.State.StartedAt}} {{.Image}}' $(docker compose ps -q app)   # igual ao anotado
# reverter: o mesmo sed com os valores invertidos e outro restart do gateway
```

Saídas observadas na troca:

```text
antes:   F1 200 em 1,106 s; /admin/calls → openai gpt-fake-large classify 200 (1077 ms)
restart: gateway healthy em 11,9 s; /model/info → classify -> openai/gpt-fake-mini 1.5e-07 6e-07
depois:  F1 200 em 0,492 s, {"ticket_id":"TK-00042","category":"delivery","priority":"high"}; /admin/calls → openai gpt-fake-mini classify 200 (374 ms)
app:     StartedAt 2026-10-10T01:01:44.960049854Z e imagem sha256:2934b5c5e621… antes e depois; mesmo id de container
erro de digitação (opnai/): [provision_keys] ERRO: deployment(s) do config.yaml descartado(s) pelo LiteLLM na subida: ['classify'] … → gateway unhealthy
corrigido: healthy em 12 s; [provision_keys] 11 deployment(s) do config.yaml carregado(s)
```
