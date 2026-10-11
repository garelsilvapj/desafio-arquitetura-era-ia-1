# 0007. Governar o consumo por chave virtual no gateway, com orçamento, limite de requisições e lista de capacidades

- Status: aceita
- Data: 2026-10-11
- Nível: solução

## Contexto

Na v1, as chaves dos providers estavam no serviço `app`, e ninguém via nem limitava o gasto. O relatório mensal abandonado custou US$ 0,85, 88% disso depois de o cliente desistir (diagnóstico no `README.md`). O [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md) fixou a política: credenciais de provider só na plataforma, uma chave por aplicação e teto de US$ 80,00 a cada 30 dias para o helpdesk. O desafio pede, além disso, demonstrar no gateway a recusa por orçamento e por limite de requisições.

Fatos do LiteLLM v1.102.1 ([ADR 0002](0002-ai-gateway-self-hosted.md)) que moldam a solução:

- Chave virtual, orçamento e gasto exigem banco ([ADR 0008](0008-banco-do-gateway.md)), e as chaves são criadas pela API de admin, não pelo `config.yaml`.
- O LiteLLM não conhece os modelos do provider-fake: sem preço declarado, o custo é zero e nenhum orçamento estoura (`provider-fake/README.md`).
- Orçamento e limite são conferidos na entrada da requisição, antes de qualquer chamada ao provider.

## Opções consideradas

1. **Sem governança**: uma chave sem limites, ou a master key na app. Não há teto nem gasto separado por aplicação, e a app ganharia poder de administração.
2. **Teto nas contas dos providers.** É compartilhado por todas as aplicações daquela conta e, com dois providers, vira duas metades fixas que não acompanham o fallback ([ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md), opção 7).
3. **Orçamento e limite por chave virtual no gateway**, somando o gasto em todos os providers.
4. **Orçamento controlado na app.** Rejeitada:
   - a app teria de saber o preço e o modelo físico que respondeu, porque o fallback muda o custo (uma `classify` custa US$ 0,0001575 no large e US$ 0,00000945 no mini), e isso devolveria à app o que o [ADR 0003](0003-capacidades-logicas-e-mapeamento.md) tirou dela;
   - a conta seria por processo, separada em cada réplica;
   - cada aplicação reimplementaria a regra, e um erro nela gastaria sem controle.

## Decisão

Opção 3. As chaves são dados, na lista `KEYS` de `gateway/provision_keys.py`. O provisionador roda dentro do container do gateway a cada start e é idempotente ([ADR 0008](0008-banco-do-gateway.md)): atualiza preservando o gasto, cria a chave ausente e faz a rotação quando o valor muda no `.env`. Os valores das chaves vêm do `.env` e só o serviço `gateway` os recebe. A app recebe só a sua.

| Alias | Capacidades | Orçamento | rpm | Quem usa e por quê |
|---|---|---|---|---|
| `helpdesk-app` | `classify`, `suggest`, `topics`, `extract` | US$ 80,00 / `30d` | sem limite | a app (`GATEWAY_API_KEY`); teto do [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md) |
| `demo-budget` | `classify` | US$ 0,000005, sem janela | sem limite | `scripts/governance-demo.sh`: a 2ª chamada estoura o orçamento |
| `demo-rpm` | `classify` | sem orçamento | 2 por minuto | `scripts/governance-demo.sh`: a 3ª chamada no minuto é recusada |

**Por que `helpdesk-app` não tem rpm baixo.** O relatório mensal manda 34 lotes ao gateway, 4 de cada vez, em ~89 s ([ADR 0004](0004-modo-de-execucao-por-feature.md)). Um rpm baixo recusaria lotes e derrubaria o relatório. O que limita o gasto é o orçamento; o limite de taxa do provider é tratado com retry e fallback ([ADR 0006](0006-resiliencia-timeout-retry-e-backoff.md)).

**Por que US$ 0,000005 na `demo-budget`.** O LiteLLM recusa quando o gasto acumulado é maior ou igual ao orçamento. A 1ª chamada sempre passa (gasto 0), e a recusa só pode vir da 2ª. Para recusar exatamente a 2ª, o teto precisa ficar abaixo do custo de **uma** `classify` do roteiro em qualquer modelo que a capacidade possa usar:

- US$ 0,0001575 no `gpt-fake-large` (19 tokens de entrada, 11 de saída);
- US$ 0,00000945 no `gpt-fake-mini`, o mais barato.

US$ 0,000005 fica abaixo dos dois. Assim a demonstração continua valendo com a `classify` trocada para o mini, e isso foi verificado. Com US$ 0,0001, ela passaria 11 chamadas no mini antes de recusar.

**Por que chaves de demonstração separadas.** Mostrar a recusa com a `helpdesk-app` exigiria gastar US$ 80 ou baixar o teto de produção. As chaves `demo-*` usam o mesmo mecanismo, limitado a `classify`, a capacidade mais barata e rápida (~1,1 s): o roteiro inteiro roda em ~6 s.

Regras que valem para todas as chaves:

- **Credenciais em um lugar só:**
  - a master key é recebida só pelo serviço `gateway`; o operador a usa do host para administrar, e o roteiro a usa para zerar o gasto da `demo-budget`;
  - as chaves dos providers chegam só ao `gateway` e ao `provider-fake`, que as valida;
  - o `app` não tem `env_file` e recebe só `GATEWAY_BASE_URL` e `GATEWAY_API_KEY`.
- **Lista de capacidades por chave.** O `/v1/models` da app lista as 4 capacidades, e o das chaves `demo-*` só `classify`. Grupo interno ou nome físico chamado com a chave da app dá `403 key_model_access_denied` ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md)).
- **A recusa acontece no gateway, antes do provider:**
  - orçamento → `429` `budget_exceeded`, em 4 ms;
  - rpm → `429` `throttling_error`, em 6 ms, com `retry-after: 60`.

  Nos dois casos, `/admin/calls` não cresce. A app não repete o `429` (`max_retries=0`, [ADR 0009](0009-portas-e-adaptadores-na-aplicacao.md)).
- **Preço declarado em todo deployment** (`input_cost_per_token`/`output_cost_per_token`, em decimal). Sem preço, o gasto fica em zero e o orçamento nunca estoura.
- **O gasto persiste** no `gateway-db` e sobrevive a `restart`, `down` e `up`. O roteiro zera o gasto da `demo-budget` no início (`POST /key/<sha256 da chave>/reset_spend`) para poder ser repetido.
- **A janela de rpm é fixa**, de 60 s a partir da 1ª requisição. Uma segunda execução logo depois encontra a janela aberta, e o roteiro espera ela fechar: a janela de rpm é fixa de 60 s; uma segunda execução logo em seguida espera a janela virar (observado entre ~58 s e ~67 s).

## Consequências

- **Melhor:**
  - O teto do [ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md) é aplicado sem código na app.
  - O gasto fica visível por chave (`spend`) e por capacidade (`/spend/logs`, com `model_group`).
  - A recusa não chega ao provider e não gasta nada.
  - Chave nova, orçamento ou limite são uma entrada na lista `KEYS`, aplicada no próximo start do gateway.
- **Pior / custo:**
  - O teto vale por chave. Quem tiver a master key cria chaves sem teto, e a master key precisa da mesma guarda das credenciais de provider.
  - Rotação do valor da chave e `docker compose down -v` zeram o gasto contado ([ADR 0008](0008-banco-do-gateway.md)).
- **Vigiar:**
  - **Janela do orçamento.** Com `budget_duration: 30d`, o LiteLLM marcou `budget_reset_at` em `2026-11-01T00:00:00Z` para a chave criada em 2026-10-10. A primeira janela acabou no início do mês seguinte, não 30 dias depois. As janelas seguintes não foram observadas.
  - **Chamadas em andamento.** Como o orçamento é conferido na entrada, as chamadas já em andamento terminam, e o estouro fica na ordem delas ([ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md)).
  - **Log.** O gateway registra cada recusa como `ERROR` (`auth_exception_handler.py`: `Budget has been exceeded!`; `Rate limit exceeded`). Alerta por contagem de `ERROR` precisa separar recusa de governança de falha.
  - **`spend` da `helpdesk-app`** em relação aos US$ 80: o alerta deve vir antes do estouro, e não com a recusa.

## Evidência

```bash
set -a; . ./.env; set +a
docker compose exec app env | grep -E "GATEWAY|FAKE|MASTER"     # só GATEWAY_API_KEY e GATEWAY_BASE_URL
docker compose config --format json | python3 -c 'import json, sys
for name, s in sorted(json.load(sys.stdin)["services"].items()): print(name, sorted(s.get("environment") or {}))'
# app ['GATEWAY_API_KEY', 'GATEWAY_BASE_URL']
# gateway [..., 'FAKE_ANTHROPIC_KEY', 'FAKE_OPENAI_KEY', 'GATEWAY_APP_KEY', 'GATEWAY_DEMO_BUDGET_KEY', 'GATEWAY_DEMO_RPM_KEY', ..., 'LITELLM_MASTER_KEY']
# provider-fake ['FAKE_ANTHROPIC_KEY', 'FAKE_OPENAI_KEY']
curl -s "localhost:4000/key/list?return_full_object=true" -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -c 'import json, sys
for k in json.load(sys.stdin)["keys"]: print(k["key_alias"], k["models"], k["max_budget"], k["budget_duration"], k["rpm_limit"], k["spend"])'
./scripts/governance-demo.sh
```

O roteiro faz, em resumo (corpo `classify` fixo, `TK-00042`):

```bash
BODY='{"model":"classify","max_tokens":2000,"messages":[{"role":"user","content":"TASK: classify\nMeu pedido #481516 não chegou e já passou do prazo, urgente"}]}'
H=$(printf %s "$GATEWAY_DEMO_BUDGET_KEY" | sha256sum | cut -d' ' -f1)
curl -s -X POST "localhost:4000/key/$H/reset_spend" -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' -d '{"reset_to": 0}'
for i in 1 2;   do curl -s -o /dev/null -w '%{http_code} ' localhost:4000/v1/chat/completions -H "Authorization: Bearer $GATEWAY_DEMO_BUDGET_KEY" -H 'Content-Type: application/json' -d "$BODY"; done   # 200 429
for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code} ' localhost:4000/v1/chat/completions -H "Authorization: Bearer $GATEWAY_DEMO_RPM_KEY"    -H 'Content-Type: application/json' -d "$BODY"; done   # 200 200 429
curl -s "localhost:8090/admin/calls?last=50"   # o roteiro compara os ids antes e depois de cada parte
```

Saídas observadas (duas execuções seguidas: 6,4 s e 64,3 s, a segunda esperando 58 s pela janela de rpm):

```text
demo-rpm ['classify'] None None 2 …  /  demo-budget ['classify'] 5e-06 None None …  /  helpdesk-app ['classify', 'suggest', 'topics', 'extract'] 80.0 30d None …
   gasto zerado (antes: US$ 0.000315; teto: US$ 5e-06)
   chamada 2 -> 429 budget_exceeded: Budget has been exceeded! Key=demo-budget (sk-...0001) Current cost: 0.00015750000000000152, Max budget: 5e-06
   chamadas novas no provider: 1
   chamada 3 -> 429 throttling_error: Rate limit exceeded for api_key: ca2c…8b13. Limit type: requests. Current limit: 2, Remaining: 0. Limit resets at: 2026-10-10 01:50:40 UTC
   chamadas novas no provider: 2
   PASS  orçamento: 1ª chamada aceita (esperado 200, obtido 200)
   PASS  orçamento: 2ª chamada recusada pelo gateway (esperado 429 budget_exceeded, obtido 429 budget_exceeded)
   PASS  orçamento: chamadas que chegaram ao provider (esperado 1, obtido 1)
   PASS  rpm: 1ª chamada aceita (esperado 200, obtido 200)
   PASS  rpm: 2ª chamada aceita (esperado 200, obtido 200)
   PASS  rpm: 3ª chamada recusada pelo gateway (esperado 429 throttling_error, obtido 429 throttling_error)
   PASS  rpm: chamadas que chegaram ao provider (esperado 2, obtido 2)
PASS: orçamento e limite de taxa recusados no gateway, sem chamada ao provider
# com a classify trocada para o mini (ADR 0003): 200 a US$ 9.45e-06, depois 429 "Current cost: 9.450000000000013e-06, Max budget: 5e-06"
```
