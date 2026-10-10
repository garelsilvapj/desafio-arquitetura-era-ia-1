# 0010. Ler a zona de dor com uma lista fechada de estáveis por natureza e regras de direção

- Status: aceita
- Data: 2026-10-09
- Nível: software

## Contexto

A métrica de acoplamento (`metrics/coupling.py`) troca opinião por medida: mostra quais componentes estão longe da Main Sequence (A + I = 1) e quais estão na **zona de dor** (A < 0,5, I < 0,5 e D ≥ 0,5), ou seja, concretos e com dependentes, que arrastam quem depende deles a cada mudança. A régua é fixa: componente = cada `.py` de `app/helpdesk` (subpastas em nome pontuado, sem `__init__`); dependência = import que resolve para outro componente; I = Ce/(Ce+Ca), ou 0; A = classes que herdam direto de `Protocol`/`ABC` ÷ classes, ou 0; D = |A + I − 1|; 2 casas.

Na v1 (`metrics/results/v1-coupled.csv`), a zona de dor era {`config`, `llm`, `schemas`}, e cada um deles dizia uma coisa diferente sobre o código:

| Componente | Ca / Ce / I / D | O que é no código |
|---|---|---|
| `llm` | 4 / 1 / 0,20 / **0,80** | Importa os SDKs `anthropic` e `openai`, cria os clientes com as chaves dos providers e expõe `call_openai`/`call_anthropic`: a feature escolhe o provider pelo nome da função. Problema real |
| `config` | 6 / 0 / 0,00 / **1,00** | É lido por 6 dos 9 módulos e mistura chaves e URLs de provider, nomes físicos de modelo por feature e limites: trocar o modelo era editar fonte. Problema real |
| `schemas` | 5 / 0 / 0,00 / **1,00** | São 7 `BaseModel` pydantic do contrato HTTP, sem comportamento nem dependência interna. Não é problema: é o contrato |

A refatoração ([plano, R1–R10 e resultado](../refactoring-plan.md)) mostrou os pontos cegos da fórmula nesta base:

- **Folha concreta sempre dá D = 1.** Todo módulo com Ce = 0, sem classe abstrata e com pelo menos um importador tem I = 0 e D = 1, tenha 1 ou 6 dependentes. O Ca de `config` caiu de 6 para 1 e o D ficou em 1,00: a rigidez que a zona quer apontar (muitos dependentes arrastados por uma mudança) caiu, mas o número não mudou.
- **O D da feature melhora com a dependência errada (R3).** Se `classification` importasse `config` para ler o nome da capacidade, teria Ce = 3, I = 0,75 e D = 0,25, melhor que os 0,33 da versão correta (só `ports` + `schemas`), e o Ca de `config` subiria para 7.
- **Exceções contam como classes concretas (R2).** Com 2 `Protocol`s + 3 exceções, `ports` cai para A = 0,40 e D = 0,60, dentro da zona. Movê-las para um módulo próprio cria outra folha concreta com D = 1.
- **A métrica não vê a direção.** Ca e Ce contam arestas, mas não para onde elas apontam. Trocar `config` + `llm` (concretos e voláteis) por `ports` (abstrato) fez o D de `classification` subir de 0,25 para 0,33.

O desafio pede que componentes concretos que quase nunca mudam sejam declarados aqui como estáveis por natureza, com justificativa, e proíbe desenhar o código em função da fórmula. Na v2 (`metrics/results/v2-decoupled.csv`), a zona de dor é {`config`, `schemas`}.

## Opções consideradas

1. **Ler a zona ao pé da letra e tirar todo componente dela.** Exemplos: uma classe `Protocol` vazia em `config.py` (A = 1, I = 0 → D = 0); imports artificiais em `schemas` até Ce ≥ Ca = 5; fundir `config` em `main` só para o módulo sumir da medição. Rejeitada porque é desenhar o código para a fórmula. Nenhuma dessas mudanças altera quem é arrastado por uma mudança, e as duas primeiras pioram a estrutura (abstração sem implementação, dependência sem uso).
2. **Declarar uma lista fechada de estáveis por natureza, com justificativa, e complementar a métrica com regras que verificam a direção das dependências.** A métrica continua apontando onde olhar, e o que ela não mede vira regra verificável por grep/AST.
3. **Ignorar a zona de dor.** Rejeitada: foi ela que apontou `llm` (D = 0,80) como o nó do acoplamento com os providers e `config` como o segundo alvo.

## Decisão

Opção 2. A lista de estáveis por natureza é fechada e tem dois itens:

- **`schemas`** (Ca = 5, Ce = 0, D = 1,00): tipos de valor pydantic do contrato **fixo** do desafio. As quatro features são fixas no que recebem e no que devolvem, e a suíte de caracterização congela esse contrato. Não têm comportamento nem dependência interna. O Ca alto é desejável, porque é o vocabulário comum de `main` e das features, e repeti-lo em cada feature seria duplicar o contrato. `schemas` só muda se o contrato mudar, o que é decisão de produto, e aí propagar a mudança às features é justamente o efeito desejado.
- **`config`** (Ca = 1, Ce = 0, D = 1,00): folha de constantes (URL e chave virtual do gateway, lidas do ambiente; timeout do cliente, `MAX_OUTPUT_TOKENS` e caminho dos tickets). Desde a v2, só o ponto de composição `main` a lê. Por isso a rigidez que a zona de dor mede não existe aqui: uma mudança em `config` arrasta um único componente. A parte volátil (chaves de provider e nomes físicos de modelo) saiu da app e mora no gateway.

**Não são exceções.** Ficam fora da zona pela estrutura, não por declaração:

- `adapters.gateway`, o único componente que chama o gateway: Ca = 1 (só `main`) e Ce = 1 (o port que implementa) → I = 0,50, D = 0,50. O mesmo vale para `adapters.tickets_file`.
- As quatro features: Ce = {`ports`, `schemas`} e Ca = 1 → I = 0,67, D = 0,33.

A herança nominal `class GatewayLanguageModel(LanguageModel)` / `class FileTicketSource(TicketSource)` é uma decisão de desenho. Ela deixa explícita a relação de implementação, faz o verificador de tipos checar a conformidade onde a classe é definida (e não só onde `main` a injeta) e põe no código-fonte a seta concreto → abstração que a inversão de dependência pede. O efeito na métrica é consequência, não motivo (R1): sem a herança, o adapter teria Ce = 0, I = 0 e D = 1, e cairia na zona.

Regras complementares, verificadas por grep/AST a cada medição porque a métrica não as captura:

| # | Regra | v1 → v2 |
|---|---|---|
| R-a | Feature importa, da app, só `ports` e `schemas` | violada (as 4 importam `config` e `llm`) → ok |
| R-b | Só `main` importa `config` e `adapters.*` | 6 importadores de `config` → só `main` |
| R-c | Cada `adapters.*` tem Ca = 1 (R1) | — → `gateway` 1, `tickets_file` 1 |
| R-d | Em `ports`, nº de classes de exceção ≤ nº de `Protocol`s (R2) | — → 0 ≤ 2 |
| R-e | Direção: arestas de feature para módulo concreto e volátil (`config`, `llm`, `tickets`, `adapters.*`) = 0 | 9 → **0**; arestas para `ports`: 0 → 6 |
| R-f | As exceções seguem cumprindo a justificativa: `schemas` sem métodos nem import interno; `config` sem `def`/`class` nem import interno | — → ok |

## Consequências

- **Melhor:** a zona de dor da v2 só contém o que não muda com provider, modelo ou política. O que ela apontou na v1 (`llm` e a parte volátil de `config`) foi corrigido, e o que ela não vê (a direção) virou regra com comando.
- **Pior / custo:** a lista é um julgamento e precisa ser relida a cada medição. As regras R-a…R-f são verificadas à mão (grep/AST) e não estão em CI. O D das features subiu de 0,20–0,25 para 0,33, e isso não é meta (R3).
- **Vigiar:**
  - Um segundo importador de `config` derruba a justificativa: `config` sai da lista e a dependência é corrigida. O D continuaria 1,00 e a métrica não avisaria; quem avisa é a R-b.
  - Um segundo importador de um adapter o põe na zona (Ca = 2, Ce = 1 → I = 0,33, D = 0,67; R1).
  - Se `schemas` ganhar comportamento (métodos com regra, import de outro componente), sai da lista (R-f).
  - Exceções novas do port (ex.: `LanguageModelUnavailable`, para o 503 da extração) respeitam a R-d.
  - Os adapters estão em I = 0,50, na fronteira e sem folga. Estão fora da zona porque o critério é I < 0,5 e porque o único componente arrastado por uma mudança neles é `main`. Não se acrescenta import a eles para ganhar margem.
  - Incluir um componente novo na lista exige a mesma justificativa, e isso nunca vale para o componente que chama o gateway nem para as features.

## Evidência

Medição (a partir da raiz do repositório; a régua é sempre o `metrics/coupling.py` atual):

```bash
# v1, a partir da tag; reproduz metrics/results/v1-coupled.csv byte a byte
git worktree add /tmp/helpdesk-v1 v1-coupled
python3 metrics/coupling.py /tmp/helpdesk-v1/app/helpdesk --name v1-coupled
git worktree remove /tmp/helpdesk-v1
# v2 (na árvore da tag v2-decoupled, ou num git worktree dela, como na v1)
python3 metrics/coupling.py app/helpdesk --name v2-decoupled
```

Linha da zona de dor impressa pelo script:

- v1: `Zona de dor (A<0.5, I<0.5, D>=0.5): config, llm, schemas`
- v2: `Zona de dor (A<0.5, I<0.5, D>=0.5): config, schemas`

Linhas relevantes dos CSVs (na v2, `extraction`, `report` e `suggestion` são iguais a `classification`):

```text
# metrics/results/v1-coupled.csv          # metrics/results/v2-decoupled.csv
classification,1,3,0.75,0.00,0.25         adapters.gateway,1,1,0.50,0.00,0.50
config,6,0,0.00,0.00,1.00                 adapters.tickets_file,1,1,0.50,0.00,0.50
llm,4,1,0.20,0.00,0.80                    classification,1,2,0.67,0.00,0.33
schemas,5,0,0.00,0.00,1.00                config,1,0,0.00,0.00,1.00
tickets,1,1,0.50,0.00,0.50                ports,6,0,0.00,1.00,0.00
                                          schemas,5,0,0.00,0.00,1.00
```

Regras complementares (resultado esperado na v2 em comentário):

```bash
# R-a: vazio (exit 1)
grep -hE "^\s*(from|import) " app/helpdesk/{classification,extraction,suggestion,report}.py \
  | grep -vE "^(import (json|re)|from fastapi import|from \.(ports|schemas) import)"
# R-b: só app/helpdesk/main.py
grep -rlE "^\s*(from|import) .*(adapters|config)" app/helpdesk
# R-c: as duas linhas com Ca = 1
grep -E "^adapters\." metrics/results/v2-decoupled.csv
# R-d: 2 Protocols e 0 outras classes
grep -cE "^class \w+\(Protocol\)" app/helpdesk/ports.py
grep -E "^class " app/helpdesk/ports.py | grep -vc "(Protocol)"
# R-f: vazio (exit 1) nos dois
grep -nE "^\s+def |^from \." app/helpdesk/schemas.py
grep -nE "^(def|class) |^from \." app/helpdesk/config.py
# R-e: com o parser do coupling.py; v2 → 0 e 6 (na árvore da v1: 9 e 0)
python3 - app/helpdesk <<'EOF'
import ast, sys; from pathlib import Path
sys.path.insert(0, "metrics"); import coupling as c
files = c.discover(Path(sys.argv[1]))
deps = {n: c.imports_of(n, ast.parse(p.read_text()), set(files), "helpdesk") for n, p in files.items()}
feats = ("classification", "extraction", "suggestion", "report")
volatil = lambda m: m in ("config", "llm", "tickets") or m.startswith("adapters.")
print("feature -> concreto volátil:", sum(volatil(m) for f in feats for m in deps[f]))
print("-> ports:", sum("ports" in d for d in deps.values()))
EOF
```

A medição da `main` é acrescentada a este ADR quando a versão final é medida.
