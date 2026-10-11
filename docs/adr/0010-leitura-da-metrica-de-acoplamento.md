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

O desafio pede que componentes concretos que quase nunca mudam sejam declarados aqui como estáveis por natureza, com justificativa, e proíbe desenhar o código em função da fórmula. Na v2 (`metrics/results/v2-decoupled.csv`), a zona de dor é {`config`, `schemas`}. Na `main` (`metrics/results/main.csv`), com os modos de execução ([ADR 0004](0004-modo-de-execucao-por-feature.md)), continua {`config`, `schemas`} (seção "Medição da `main`").

## Opções consideradas

1. **Ler a zona ao pé da letra e tirar todo componente dela.** Exemplos: uma classe `Protocol` vazia em `config.py` (A = 1, I = 0 → D = 0); imports artificiais em `schemas` até Ce ≥ Ca = 5; fundir `config` em `main` só para o módulo sumir da medição. Rejeitada porque é desenhar o código para a fórmula. Nenhuma dessas mudanças altera quem é arrastado por uma mudança, e as duas primeiras pioram a estrutura (abstração sem implementação, dependência sem uso).
2. **Declarar uma lista fechada de estáveis por natureza, com justificativa, e complementar a métrica com regras que verificam a direção das dependências.** A métrica continua apontando onde olhar, e o que ela não mede vira regra verificável por grep/AST.
3. **Ignorar a zona de dor.** Rejeitada: foi ela que apontou `llm` (D = 0,80) como o nó do acoplamento com os providers e `config` como o segundo alvo.

## Decisão

Opção 2. A lista de estáveis por natureza é fechada e tem dois itens:

- **`schemas`** (Ca = 5 na v2 e 6 na `main`, Ce = 0, D = 1,00): tipos de valor pydantic do contrato HTTP definido pelo desafio. O que cada feature recebe e devolve só muda por decisão registrada, e a suíte de caracterização congela esse contrato. Não têm comportamento nem dependência interna. O Ca alto é desejável, porque é o vocabulário comum de `main`, das features e (na `main`) de `jobs`, e repeti-lo em cada um seria duplicar o contrato. `schemas` só muda se o contrato mudar, o que é decisão registrada, e aí propagar a mudança é justamente o efeito desejado. Foi o que aconteceu na `main`: o contrato da F3 passou a ser assíncrono ([ADR 0004](0004-modo-de-execucao-por-feature.md)), e `schemas` ganhou `JobStatus`, o corpo do estado do job, ainda sem comportamento.
- **`config`** (Ca = 1, Ce = 0, D = 1,00): folha de constantes (URL e chave virtual do gateway, lidas do ambiente; timeout do cliente, `MAX_OUTPUT_TOKENS` e caminho dos tickets; na `main`, também quantos lotes da F3 rodam em paralelo e quantos jobs ficam na memória). Desde a v2, só o ponto de composição `main` a lê. Por isso a rigidez que a zona de dor mede não existe aqui: uma mudança em `config` arrasta um único componente. A parte volátil (chaves de provider e nomes físicos de modelo) saiu da app e mora no gateway.

**Não são exceções.** Ficam fora da zona pela estrutura, não por declaração:

- `adapters.gateway`, o único componente que chama o gateway: Ca = 1 (só `main`) e Ce = 1 (o port que implementa) → I = 0,50, D = 0,50. O mesmo vale para `adapters.tickets_file`.
- As quatro features: Ce = {`ports`, `schemas`} e Ca = 1 → I = 0,67, D = 0,33.
- `jobs` (só na `main`), o registro dos jobs da F3: Ca = 1 (só `main`) e Ce = {`ports`, `schemas`} → I = 0,67, D = 0,33. Os dois imports são usados: devolve o `JobStatus` e guarda o `TopicsReport` de `schemas`, e separa a falha esperada (`LanguageModelUnavailable`, de `ports`, que vira o `reason` do job) de qualquer outra.
- `ports` na `main`: 2 `Protocol`s + 1 exceção → A = 0,67, I = 0, D = 0,33. Fora da zona porque A ≥ 0,5, dentro do limite da R-d (R2).

A herança nominal `class GatewayLanguageModel(LanguageModel)` / `class FileTicketSource(TicketSource)` é uma decisão de desenho. Ela deixa explícita a relação de implementação, faz o verificador de tipos checar a conformidade onde a classe é definida (e não só onde `main` a injeta) e põe no código-fonte a seta concreto → abstração que a inversão de dependência pede. O efeito na métrica é consequência, não motivo (R1): sem a herança, o adapter teria Ce = 0, I = 0 e D = 1, e cairia na zona.

Regras complementares, verificadas por grep/AST a cada medição porque a métrica não as captura:

| # | Regra | v1 → v2 | `main` |
|---|---|---|---|
| R-a | Feature importa, da app, só `ports` e `schemas` (de fora da app, só biblioteca padrão e `fastapi`) | violada (as 4 importam `config` e `llm`) → ok | ok (de fora: `asyncio`, `collections.abc`, `json`, `re`, `fastapi`) |
| R-b | Só `main` importa `config`, `adapters.*` e `jobs` | 6 importadores de `config` → só `main` | só `main` |
| R-c | Cada `adapters.*` tem Ca = 1 (R1) | — → `gateway` 1, `tickets_file` 1 | `gateway` 1, `tickets_file` 1 |
| R-d | Em `ports`, nº de classes de exceção ≤ nº de `Protocol`s (R2) | — → 0 ≤ 2 | 1 ≤ 2 (`LanguageModelUnavailable`) |
| R-e | Direção: arestas de feature para módulo concreto e volátil (`config`, `llm`, `tickets`, `jobs`, `adapters.*`) = 0 | 9 → **0**; arestas para `ports`: 0 → 6 | **0**; arestas para `ports`: 7 (+ `jobs`) |
| R-f | As exceções seguem cumprindo a justificativa: `schemas` sem métodos nem import interno; `config` sem `def`/`class` nem import interno | — → ok | ok |

## Consequências

- **Melhor:** a zona de dor da v2 e da `main` só contém o que não muda com provider, modelo ou política. O que ela apontou na v1 (`llm` e a parte volátil de `config`) foi corrigido, e o que ela não vê (a direção) virou regra com comando.
- **Pior / custo:** a lista é um julgamento e precisa ser relida a cada medição. As regras R-a…R-f são verificadas à mão (grep/AST) e não estão em CI. O D das features subiu de 0,20–0,25 para 0,33, e isso não é meta (R3).
- **Vigiar:**
  - Um segundo importador de `config` derruba a justificativa: `config` sai da lista e a dependência é corrigida. O D continuaria 1,00 e a métrica não avisaria; quem avisa é a R-b.
  - Um segundo importador de um adapter o põe na zona (Ca = 2, Ce = 1 → I = 0,33, D = 0,67; R1).
  - Se `schemas` ganhar comportamento (métodos com regra, import de outro componente), sai da lista (R-f).
  - Exceções do port respeitam a R-d. Na `main` há uma, `LanguageModelUnavailable` (A = 0,67). Uma segunda levaria `ports` a A = 0,50, na fronteira; uma terceira, à zona (A = 0,40, D = 0,60; R2).
  - Os adapters estão em I = 0,50, na fronteira e sem folga. Estão fora da zona porque o critério é I < 0,5 e porque o único componente arrastado por uma mudança neles é `main`. Não se acrescenta import a eles para ganhar margem.
  - Incluir um componente novo na lista exige a mesma justificativa, e isso nunca vale para o componente que chama o gateway nem para as features.

## Evidência

Medição (a partir da raiz do repositório; a régua é sempre o `metrics/coupling.py` atual):

```bash
# main (a árvore atual); reproduz metrics/results/main.csv byte a byte
python3 metrics/coupling.py app/helpdesk --name main
# uma tag, sem mexer na cópia de trabalho; reproduz metrics/results/<tag>.csv byte a byte
git worktree add /tmp/helpdesk-v1 v1-coupled
python3 metrics/coupling.py /tmp/helpdesk-v1/app/helpdesk --name v1-coupled
git worktree remove /tmp/helpdesk-v1
# v2: o mesmo, com v2-decoupled
```

Linha da zona de dor impressa pelo script:

- v1: `Zona de dor (A<0.5, I<0.5, D>=0.5): config, llm, schemas`
- v2: `Zona de dor (A<0.5, I<0.5, D>=0.5): config, schemas`
- `main`: `Zona de dor (A<0.5, I<0.5, D>=0.5): config, schemas`

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

Regras complementares. Os comandos valem para as três árvores (numa tag, troque `app/helpdesk` pelo caminho do worktree); o resultado esperado está em comentário:

```bash
# R-a: vazio (exit 1) na v2 e na main; na v1, as 4 linhas "from . import config, llm[, tickets]"
grep -hE "^\s*(from|import) (\.|helpdesk)" app/helpdesk/{classification,extraction,suggestion,report}.py \
  | grep -vE "^from \.(ports|schemas) import"
# R-a, de fora da app: só biblioteca padrão e fastapi (main: asyncio, collections.abc, fastapi, json, re)
grep -hE "^\s*(from|import) [^.]" app/helpdesk/{classification,extraction,suggestion,report}.py | sort -u
# R-b: só app/helpdesk/main.py (na v1: as 4 features, llm e tickets)
grep -rlE "^\s*(from|import) .*(adapters|config|jobs)" app/helpdesk
# R-c: as duas linhas com Ca = 1
grep -E "^adapters\." metrics/results/main.csv
# R-d: nº de Protocols e de outras classes (main: 2 e 1; v2: 2 e 0)
grep -cE "^class \w+\(Protocol\)" app/helpdesk/ports.py
grep -E "^class " app/helpdesk/ports.py | grep -vc "(Protocol)"
# R-f: vazio (exit 1) nos dois
grep -nE "^\s+def |^from \." app/helpdesk/schemas.py
grep -nE "^(def|class) |^from \." app/helpdesk/config.py
# R-e: com o parser do coupling.py; main → 0 e 7; v2 → 0 e 6; v1 → 9 e 0
python3 - app/helpdesk <<'PY'
import ast, sys; from pathlib import Path
sys.path.insert(0, "metrics"); import coupling as c
files = c.discover(Path(sys.argv[1]))
deps = {n: c.imports_of(n, ast.parse(p.read_text()), set(files), "helpdesk") for n, p in files.items()}
feats = ("classification", "extraction", "suggestion", "report")
volatil = lambda m: m in ("config", "llm", "tickets", "jobs") or m.startswith("adapters.")
print("feature -> concreto volátil:", sum(volatil(m) for f in feats for m in deps[f]))
print("-> ports:", sum("ports" in d for d in deps.values()))
PY
```

### Medição da `main`

Medida em 2026-10-11, depois dos modos de execução ([ADR 0004](0004-modo-de-execucao-por-feature.md)): `python3 metrics/coupling.py app/helpdesk --name main` → `metrics/results/main.csv` e `.svg`. Recalculada nesta árvore, ficou idêntica à versionada (`diff` e `cmp` vazios).

```text
component              ca  ce     i     a     d
adapters.gateway        1   1  0.50  0.00  0.50
adapters.tickets_file   1   1  0.50  0.00  0.50
classification          1   2  0.67  0.00  0.33
config                  1   0  0.00  0.00  1.00
extraction              1   2  0.67  0.00  0.33
jobs                    1   2  0.67  0.00  0.33
main                    0   9  1.00  0.00  0.00
ports                   7   0  0.00  0.67  0.33
report                  1   2  0.67  0.00  0.33
schemas                 6   0  0.00  0.00  1.00
suggestion              1   2  0.67  0.00  0.33

Zona de dor (A<0.5, I<0.5, D>=0.5): config, schemas
```

Leitura, v2 → `main`:

- **A zona de dor não mudou:** {`config`, `schemas`}, as duas exceções declaradas acima, com a justificativa ainda válida (R-b e R-f ok).
- **`ports`**: Ca 6 → 7 (`jobs` passou a depender dele) e A 1,00 → 0,67, porque ganhou a exceção `LanguageModelUnavailable` (o método `stream`, também novo, não muda o A). D 0,00 → 0,33, fora da zona (A ≥ 0,5). É o efeito previsto na R2: uma exceção basta para os três modos de falha (`503`, `event: error`, job `failed`), e a R-d continua valendo (1 ≤ 2).
- **`jobs`** (novo): Ca = 1, Ce = 2, I = 0,67, D = 0,33, como as features. Não é exceção e não precisou de import artificial para ficar fora da zona.
- **`schemas`**: Ca 5 → 6 (`jobs`), por causa do `JobStatus`. Continua só com tipos de valor.
- **`main`**: Ce 8 → 9 (`jobs`). Não importa `ports`: o SSE da F2 trata qualquer exceção depois do `200` como `event: error`. A formatação SSE ficou em `main` (camada web): um módulo `sse.py` importado só por `main`, sem import interno, seria mais uma folha concreta com D = 1, na zona, sem ganho de desenho.
- **Features e adapters**: iguais à v2 (I = 0,67 e 0,50). O componente que chama o gateway continua sendo `adapters.gateway`, importado só por `main`.

Regras na `main` (comandos acima, rodados em 2026-10-11): R-a vazio (exit 1); de fora da app, `asyncio`, `collections.abc`, `fastapi`, `json` e `re`; R-b só `app/helpdesk/main.py`; R-c `adapters.gateway,1,1,…` e `adapters.tickets_file,1,1,…`; R-d 2 e 1; R-e 0 e 7; R-f vazio nos dois.
