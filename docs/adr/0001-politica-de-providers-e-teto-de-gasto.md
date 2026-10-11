# 0001. Consumir modelos só pelo gateway corporativo, com dois providers por capacidade e teto de US$ 80 a cada 30 dias

- Status: aceita
- Data: 2026-10-09
- Nível: corporativa

## Contexto

Esta é uma política da empresa para toda feature de IA, não só do helpdesk; o helpdesk é a primeira aplicação a segui-la. Três fatos medidos na v1 (diagnóstico no `README.md`) a motivaram:

- **Um provider é um ponto único de falha.** Cada feature estava atada a um provider. Com a Anthropic em `error_500`, sugestão (F2) e relatório (F3) responderam 500; com a OpenAI, classificação (F1) e extração (F4). Um provider fora do ar derrubou metade do helpdesk. Pendurado foi pior: com a Anthropic em `timeout`, o atendente recebeu 504 da borda aos 30 s, e a thread da app ficou presa até ~900 s por requisição (3 tentativas × 300 s).
- **As credenciais dos providers estavam na app.** `FAKE_OPENAI_KEY`/`FAKE_ANTHROPIC_KEY` chegavam ao serviço `app` por `env_file: .env`, e `llm.py` importava os dois SDKs. Cada aplicação com IA seria mais um lugar com chave de provider para rotacionar e vazar.
- **Ninguém via nem limitava o gasto.** O relatório mensal abandonado (504 aos 30 s) custou US$ 0,8488 em 34 chamadas. Dessas, as iniciadas depois de o cliente desistir custaram US$ 0,7476 (88%). Não havia contador de gasto por aplicação nem teto: o custo só apareceria na fatura do provider.

Dois fatos tornam a redundância viável e dão a base da conta:

- **Os modelos large dos dois providers são intercambiáveis.** Deram saída idêntica em 400/400 chamadas de `classify`/`extract`, em 3 lotes de `topics` e em 5 de `suggest`: trocar de provider não muda a resposta.
- **Volume e preços de referência.** Agosto de 2026 teve 5.000 tickets (`data/tickets.jsonl`). Preços declarados, em US$ por 1M tokens de entrada/saída: `gpt-fake-large` 2,50/10,00 e `claude-fake-large` 3,00/15,00.

## Opções consideradas

Acesso aos modelos:

1. Cada aplicação com SDK e credenciais dos providers (a v1).
2. Gateway SaaS. Rejeitada: o texto dos tickets (com número de pedido e de nota fiscal do cliente) e as credenciais dos providers sairiam da empresa, e a plataforma precisa rodar localmente.
3. Gateway self-hosted corporativo, único caminho até os providers.

Providers:

4. Um provider só, com contrato mais simples.
5. Pelo menos dois providers contratados, com modelo equivalente para cada capacidade.

Gasto:

6. Sem teto, acompanhando a fatura.
7. Teto na conta de cada provider. É compartilhado por todas as aplicações daquela conta e não separa o helpdesk das demais. Com dois providers, vira duas metades fixas que não acompanham o fallback: com a Anthropic fora, todo o tráfego vai para a OpenAI.
8. Teto por chave de aplicação no gateway, somando o gasto em todos os providers.

## Decisão

Opções 3, 5 e 8. A política tem três regras.

**(a) Toda feature de IA consome modelo só pelo gateway corporativo self-hosted** ([ADR 0002](0002-ai-gateway-self-hosted.md)), com nomes lógicos de capacidade e uma chave virtual própria. Credenciais de provider pertencem à plataforma e ficam só no gateway: nenhuma aplicação as recebe.

**(b) Toda capacidade tem modelo equivalente em pelo menos dois providers contratados**, e o gateway troca de um para o outro ([ADR 0003](0003-capacidades-logicas-e-mapeamento.md) e [ADR 0005](0005-politica-de-fallback.md)). Só se contrata provider alcançável a partir do gateway self-hosted.

**(c) O teto de gasto de IA do helpdesk é US$ 80,00 a cada 30 dias.** É aplicado como orçamento da chave virtual `helpdesk-app` (`max_budget` 80, `budget_duration` 30d; [ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md)). A conta usa os preços declarados e os 5.000 tickets de agosto: cada ticket passa uma vez por F1, F2 e F4, e o relatório do mês inteiro roda 4 vezes por mês (margem para reemissões).

| Item | Chamadas/mês | Tokens de entrada / saída | US$ |
|---|---|---|---|
| F2 sugestão (`claude-fake-large`, ~608 tokens de saída por ticket) | 5.000 | 226.336 / 3.039.278 | 46,27 |
| F1 classificação (`gpt-fake-large`) | 5.000 | 227.672 / 60.605 | 1,18 |
| F4 extração (`gpt-fake-large`) | 5.000 | 226.336 / 56.567 | 1,13 |
| F3 relatório do mês (`claude-fake-large`, 34 lotes de 150) × 4 | 136 | 4 × 222.114 / 4 × 12.162 | 4 × 0,85 = 3,40 |
| **Total** | | | **51,97** |

US$ 80 ≈ 1,5 × 52 (1,54×). A folga de ~US$ 28 cobre:

- retentativas, fallbacks e chamadas que seguem depois de o cliente desistir (um relatório mensal abandonado custa US$ 0,75);
- crescimento: a ~US$ 0,0097 por ticket em F1+F2+F4, cabem ~2.900 tickets a mais por mês.

O fallback entre os large quase não mexe na conta: F1 e F4 inteiras no `claude-fake-large` somariam US$ 0,81 no mês, e F2 no `gpt-fake-large` sairia mais barata (US$ 30,96).

**Ao estourar, o gateway recusa.** O orçamento é conferido na autenticação da chave: o LiteLLM responde `429` (`budget_exceeded`) sem chamar o provider (`BudgetExceededError` → 429 em `litellm/proxy/auth/auth_exception_handler.py`). A empresa prefere deixar as features de IA indisponíveis até a janela de 30 dias virar a gastar além do teto.

**Quem decide e por quanto tempo.** Finanças aprova o teto. A plataforma opera o gateway, guarda as credenciais e provisiona as chaves. O produto (dono do helpdesk) aceita a indisponibilidade quando o teto estoura, e segurança aprova a lista de providers. A política é revista uma vez por ano, junto com os contratos dos providers, ou antes, se:

- o volume mensal se afastar mais de 20% dos 5.000 tickets;
- os preços mudarem;
- o helpdesk ganhar uma feature de IA nova.

## Consequências

- **Melhor:**
  - Uma queda de provider deixa de ser uma queda do helpdesk, desde que o fallback do [ADR 0005](0005-politica-de-fallback.md) esteja configurado.
  - Nenhuma aplicação guarda chave de provider: na v2, o `app` só recebe `GATEWAY_BASE_URL` e `GATEWAY_API_KEY`.
  - O gasto fica visível por chave (`spend`) e por capacidade (`/spend/logs`) antes da fatura.
- **Pior / custo:**
  - Dois contratos para manter.
  - O gateway vira caminho obrigatório: se ele cai, caem todas as features de IA ([ADR 0002](0002-ai-gateway-self-hosted.md)).
  - Um pico legítimo pode desligar as features de IA no meio do mês. Subir o teto é decisão corporativa (finanças + produto), não do operador de plantão.
- **Passa a ser necessário:**
  - Declarar no gateway o preço de todo modelo físico e atualizá-lo quando o contrato mudar. Sem preço, o LiteLLM calcula custo zero e o teto nunca estoura (`provider-fake/README.md`).
  - Acompanhar o `spend` da chave antes que ele chegue ao teto ([ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md)).
- **Vigiar:**
  - O teto é conferido antes de cada chamada, então as que já estão em andamento quando ele é cruzado terminam. O estouro fica na ordem dessas chamadas: um lote de `topics` custa ~US$ 0,025 e uma sugestão ~US$ 0,009.
  - `docker compose down -v` zera o gasto acumulado ([ADR 0008](0008-banco-do-gateway.md)), e a rotação do valor da chave também: a chave nova começa do zero.

## Evidência

```bash
set -a; . ./.env; set +a       # GATEWAY_APP_KEY e LITELLM_MASTER_KEY
# (a) a app não tem credencial de provider; só o gateway (e o provider-fake, que as valida) as recebe
docker compose exec app env | grep -iE "fake|anthropic|openai_api"   # vazio
grep -rE "FAKE_|sk-fake|sk-ant-fake" app/                             # vazio
docker compose config gateway | grep -E "FAKE_(OPENAI|ANTHROPIC)_KEY" # as duas
# (b) cada capacidade com deployment ou fallback nos dois providers (ADR 0003 e ADR 0005)
grep -nE "model_name|model: (openai|anthropic)/|fallbacks" gateway/config.yaml
# (c) teto na chave da app: esperado max_budget 80.0 e budget_duration 30d
curl -s "localhost:4000/key/list?key_alias=helpdesk-app&return_full_object=true" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -c 'import json, sys
k = json.load(sys.stdin)["keys"][0]; print({f: k[f] for f in ("key_alias", "spend", "max_budget", "budget_duration")})'
```

A recusa com `429` antes do provider, quando o orçamento acaba, é verificada com a chave de demonstração `demo-budget` (orçamento de US$ 0,000005, em `gateway/provision_keys.py`), na evidência do [ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md).

Conta do teto, a partir da raiz do repositório. Os tokens seguem a regra do provider-fake (`ceil(caracteres / 4)`) e as saídas vêm do seu gerador determinístico. A conta reproduz os 222.114 / 12.162 tokens do relatório mensal medidos no diagnóstico.

```bash
python3 - <<'EOF'
import json, math, importlib.util
spec = importlib.util.spec_from_file_location("tasks", "provider-fake/app/tasks.py")
tasks = importlib.util.module_from_spec(spec); spec.loader.exec_module(tasks)
tok = lambda s: math.ceil(len(s) / 4)
price = {"classify": (2.5, 10), "extract": (2.5, 10), "suggest": (3, 15), "topics": (3, 15)}  # US$ por 1M (large)
aug = [t for t in map(json.loads, open("data/tickets.jsonl")) if t["created_at"].startswith("2026-08")]
def cost(task, content):
    i, o = price[task]
    return (tok(f"TASK: {task}\n{content}") * i + tok(tasks.generate(task, content, "large")) * o) / 1e6
c = {t: sum(cost(t, x["text"]) for x in aug) for t in ("suggest", "classify", "extract")}
c["topics"] = sum(cost("topics", "\n".join(f"[{x['id']}] {x['text']}" for x in aug[i:i + 150])) for i in range(0, len(aug), 150))
print(len(aug), {k: round(v, 2) for k, v in c.items()}, "F1+F2+F4+4xF3 =", round(sum(c.values()) + 3 * c["topics"], 2))
EOF
# → 5000 {'suggest': 46.27, 'classify': 1.18, 'extract': 1.13, 'topics': 0.85} F1+F2+F4+4xF3 = 51.97
```
