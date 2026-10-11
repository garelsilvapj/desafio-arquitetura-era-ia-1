# 0002. Pôr um AI gateway self-hosted (LiteLLM Proxy) entre a app e os providers

- Status: aceita
- Data: 2026-10-09
- Nível: solução

## Contexto

A política corporativa ([ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md)) exige que toda feature de IA consuma modelos só por um gateway self-hosted, com as credenciais dos providers fora da app, dois providers por capacidade e teto de gasto por chave. Na v1, a app fazia tudo isso sozinha, ou não fazia (diagnóstico no `README.md`):

- **Provider fixo na feature.** `llm.py` importava os SDKs `openai` e `anthropic` e criava os clientes com as chaves dos providers. A feature escolhia o provider pelo nome da função (`call_openai`/`call_anthropic`).
- **Trocar de modelo era deploy.** Para levar a classificação ao `gpt-fake-mini`, foi preciso editar `config.py` e reconstruir a imagem. Com `restart`, a app continuou em `gpt-fake-large`; só `up -d --build` trocou o modelo (Dor 4).
- **Política herdada, não escolhida.** A política de chamada era a dos SDKs: timeout de leitura de 600 s e 2 retentativas. Com o provider pendurado, a thread ficava presa até ~900 s.
- **Faltava o resto.** Não havia fallback, gasto por aplicação nem limite.

Este ADR decide qual gateway usar e onde ele fica.

## Opções consideradas

1. **Manter os SDKs na app** (a v1). Viola o [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md) (chaves na app) e mantém as Dores 3 e 4.
2. **Biblioteca de roteamento dentro da app** (SDK do LiteLLM, `litellm.Router`). Dá nomes lógicos, retry e fallback, mas:
   - as chaves dos providers continuam no processo da app;
   - trocar de modelo continua sendo deploy da app;
   - orçamento e limite valem por processo (cada réplica conta o próprio gasto);
   - cada aplicação nova da empresa repete a configuração.
3. **Gateway self-hosted como serviço próprio** (LiteLLM Proxy).
4. **Gateway SaaS.** Proibido pelo enunciado e pelo [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md): dados e credenciais sairiam da empresa, e a plataforma precisa rodar localmente.

## Decisão

Opção 3: LiteLLM Proxy, imagem `ghcr.io/berriai/litellm:v1.102.1`, como serviço `gateway` do compose. O caminho de toda chamada de modelo fica `edge → app → gateway → provider`:

- A app fala com o gateway na API compatível com OpenAI (`http://gateway:4000/v1`). Manda como `model` o nome lógico da capacidade (`classify`, `suggest`, `topics`, `extract`) e se autentica com a chave virtual `helpdesk-app`.
- Modelo físico, provider, credenciais e preço de cada capacidade ficam em `gateway/config.yaml` e no ambiente do serviço `gateway`.
- O `app` só sobe com o gateway `healthy` (`depends_on: condition: service_healthy`).

Por que o LiteLLM:

- chaves virtuais com lista de modelos permitidos, orçamento e limite de requisições ([ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md));
- grupos de modelo por nome lógico ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md));
- retry, fallback e timeout por deployment ([ADR 0005](0005-politica-de-fallback.md), [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md));
- API compatível com OpenAI, de modo que a app usa um SDK só, sem o formato de cada provider;
- o provider-fake foi testado com esta versão (`provider-fake/README.md`).

Custos e limitações encontrados ao adotá-lo, e como cada um foi tratado:

| Achado no LiteLLM v1.102.1 | Tratamento |
|---|---|
| Chave virtual, orçamento e gasto exigem banco | Serviço `gateway-db` (Postgres), [ADR 0008](0008-banco-do-gateway.md) |
| Não aceita chaves declaradas no `config.yaml`: elas são criadas pela API de admin | Provisionamento idempotente no próprio container: `gateway/entrypoint.sh` dispara `gateway/provision_keys.py` e entrega o processo ao `litellm`. O healthcheck exige `/health/readiness` **e** `/tmp/keys.ok` ([ADR 0008](0008-banco-do-gateway.md)) |
| Sem a flag, busca no GitHub o mapa de custos a cada start. Com o GitHub bloqueado: 3 tentativas e ~8 s de avisos no log | `LITELLM_LOCAL_MODEL_COST_MAP=True`. `LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True` por defesa: a lista de betas da Anthropic só é buscada quando chega o header `anthropic-beta`, que a app não envia |
| Não conhece os modelos do provider-fake: sem preço declarado, o custo é 0 e nenhum orçamento estoura. Preço em notação científica (`3e-6`) o PyYAML lê como string, e o custo também vira 0 | `input_cost_per_token`/`output_cost_per_token` por deployment, em decimal; conferido em `/spend/logs` (11 + 12 tokens = US$ 0,0001475) |
| `/health` chama todos os modelos: aparece em `/admin/calls` e gasta | O healthcheck usa `/health/readiness`; `/health` nunca entra nele |
| `/key/info` de chave inexistente loga `ERROR` com traceback | O provisionador consulta `/key/list?key_alias=…`; a subida a frio fica sem `ERROR`/`Traceback` no log |
| Sem política declarada, o roteador repete 2 vezes. 1 chamada da app (`max_retries=0`) virou 3 no provider, e o 500 saiu em 4,23 s (R6 do [plano](../refactoring-plan.md)) | Política explícita, [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md) |
| Sem `max_tokens`, aplica um padrão próprio (`DEFAULT_MAX_TOKENS` = 4096) | A app sempre envia `max_tokens` ([ADR 0009](0009-portas-e-adaptadores-na-aplicacao.md)) |
| Com o `config.yaml` montado como arquivo, edições que trocam o inode não chegam ao container | Monta o diretório: `./gateway:/config:ro` |
| Imagem de 1,19 GB, sem `curl` | Healthcheck e provisionador em `python3`, só com biblioteca padrão |

## Consequências

- **Melhor:**
  - A app não conhece provider: usa um SDK só (`openai`), não guarda chave de provider e não cita nenhum nome físico de modelo (`grep` vazio em `app/`).
  - Trocar o modelo de uma capacidade é editar `gateway/config.yaml` e rodar `docker compose restart gateway`. A app não é reconstruída nem reiniciada ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md)).
  - Uma chamada com nome físico usando a chave da app é recusada com `403 key_model_access_denied` e não chega ao provider: 3 chamadas ao gateway geraram 2 entradas em `/admin/calls`.
- **Um salto a mais, sem custo visível em operação normal.** Pela borda, as F2–F4 foram medidas no piloto (direto no SDK) e na v2 (pelo gateway):
  - extração: 1,09 s → 1,09 s;
  - sugestão: 16,14 s → 16,15 s;
  - relatório de um dia: 9,84 s → 9,83 s.

  A classificação leva 1,10 s pela borda para 1.076 ms no provider: borda, app e gateway somam ~20 ms. Em falha é diferente: o 500 sai em 4–5 s, em vez de 1,3–1,5 s, por causa das retentativas padrão do gateway (R10). O [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md) as torna explícitas.
- **Ponto único de operação.** Se o gateway cai, caem as quatro features (na v1, a queda de um provider levava metade). Por isso ele está no compose, com healthcheck, e a app depende dele `healthy`. Medido:
  - `docker compose restart gateway` com a app no ar: volta a `healthy` em 11,3 s, e a F1 respondeu 200 nas 3 chamadas seguintes, sem reiniciar a app;
  - subida a frio (`down -v` + `up --build --wait`): 27 s no total, ~15 s do start do gateway ao `healthy`.

  Em produção, isso pede mais de uma réplica atrás de um balanceador, compartilhando o banco. Isso fica fora deste repositório.
- **Vigiar:**
  - Sem streaming, o gateway não cancela a chamada ao provider quando a app desiste. Na v2, com a Anthropic em `timeout`, a F2 (ainda síncrona) respondeu 500 aos 25,0 s (rede de segurança da app), e a chamada seguiu aberta no provider (R10). Na `main`, a F2 usa streaming, e a desistência chega ao provider: o cliente que desiste aos 3 s encerra a chamada em 3.021 ms. Sem streaming (prazos da F1 e da F4, lotes cancelados da F3), a limitação continua, tratada no [ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md).
  - A imagem está fixada por tag. Subir a versão do LiteLLM exige refazer as verificações da tabela.
  - A porta `4000` é publicada para o avaliador. Os endpoints de admin exigem a master key, que só o serviço `gateway` recebe.

## Evidência

```bash
set -a; . ./.env; set +a
docker compose ps gateway gateway-db app                 # todos healthy
# a app só vê nomes lógicos
curl -s localhost:4000/v1/models -H "Authorization: Bearer $GATEWAY_APP_KEY"
# → ids classify, suggest, topics, extract
# nome físico com a chave da app → 403 key_model_access_denied, sem entrada nova em /admin/calls
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $GATEWAY_APP_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-fake-large","max_tokens":50,"messages":[{"role":"user","content":"TASK: classify\noi"}]}'
# a app só conhece o gateway
docker compose exec app env | cut -d= -f1                # GATEWAY_API_KEY, GATEWAY_BASE_URL e as da imagem python
grep -rln openai app/helpdesk                            # só app/helpdesk/adapters/gateway.py
grep -rE "gpt-fake|claude-fake|FAKE_|sk-fake|sk-ant-fake|anthropic" app/   # vazio
# healthcheck sem /health; subida a frio sem erro
docker compose config gateway | grep -A1 "test:"         # /health/readiness + os.stat('/tmp/keys.ok')
docker compose logs gateway | grep -cE "ERROR|Traceback" # 0 logo após down -v + up
```

Na tag `v2-decoupled`, depois de `curl -s -X POST localhost:8090/admin/reset` e da suíte de caracterização (`20 passed`), as quatro capacidades passam pelo gateway:

```text
/admin/calls (task, provider, model, status) → n      gateway /spend/logs (model_group → modelo) → n
classify  openai     gpt-fake-large     200   6        classify → openai/gpt-fake-large          6
extract   openai     gpt-fake-large     200   3        extract  → openai/gpt-fake-large          3
suggest   anthropic  claude-fake-large  200   2        suggest  → anthropic/claude-fake-large    2
topics    anthropic  claude-fake-large  200   1        topics   → anthropic/claude-fake-large    1   (US$ 0,0438 no total)
```

O log de acesso do gateway tem as 12 linhas `POST /v1/chat/completions` correspondentes, todas vindas do IP do `app`.
