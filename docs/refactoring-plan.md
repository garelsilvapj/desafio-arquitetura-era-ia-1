# Plano de refatoração: da v1 acoplada à v2 desacoplada

Este é o **plano original**, escrito em 2026-10-09, antes de qualquer mudança no código da app (`app/helpdesk/` idêntico à tag `v1-coupled`). Ele não é reescrito depois: o que o piloto ou a refatoração em escala mostrarem de diferente entra em [Revisões do plano](#revisões-do-plano), com o número que motivou a mudança.

Régua: `metrics/coupling.py` (Ca, Ce, I = Ce/(Ce+Ca), A = classes abstratas/classes, D = |A + I − 1|). **Zona de dor** = A < 0,5, I < 0,5 e D ≥ 0,5 (concreto, muito usado e difícil de mudar). Juízes de toda mudança: a suíte de caracterização (`./tests/run.sh characterization`, 20 testes, inalterada) e a métrica.

## Ponto de partida

Medição da v1 (`metrics/results/v1-coupled.csv`):

| Componente | Ca | Ce | I | A | D | Zona de dor? |
|---|---|---|---|---|---|---|
| classification | 1 | 3 | 0,75 | 0,00 | 0,25 | não |
| config | 6 | 0 | 0,00 | 0,00 | **1,00** | **sim** |
| extraction | 1 | 3 | 0,75 | 0,00 | 0,25 | não |
| llm | 4 | 1 | 0,20 | 0,00 | **0,80** | **sim** |
| main | 0 | 5 | 1,00 | 0,00 | 0,00 | não |
| report | 1 | 4 | 0,80 | 0,00 | 0,20 | não |
| schemas | 5 | 0 | 0,00 | 0,00 | **1,00** | **sim** |
| suggestion | 1 | 3 | 0,75 | 0,00 | 0,25 | não |
| tickets | 1 | 1 | 0,50 | 0,00 | 0,50 | não (no limite: I = 0,5) |

Nenhum componente tem abstração (A = 0 em todos): todas as dependências apontam para código concreto.

**`llm` (D = 0,80)** é o nó por onde passa todo o acoplamento com os providers:

- `llm.py:3-4` importa os dois SDKs (`anthropic` e `openai`); `llm.py:8-9` cria os dois clientes no import do módulo com `config.OPENAI_API_KEY`/`config.ANTHROPIC_API_KEY` e as URLs dos providers, sem `timeout` nem `max_retries` (padrões de 600 s e 2 retentativas, ver Diagnóstico no README).
- A escolha do provider vaza para o nome da função que a feature chama: `call_openai` em `classification.py:11` e `extraction.py:12`, `call_anthropic` em `suggestion.py:6` e `report.py:18`. Trocar o provider de uma feature é editar a feature.
- É importado por 4 features (Ca = 4) e não tem nenhuma abstração: qualquer mudança de SDK, provider ou política de chamada propaga para as quatro.

**`config` (D = 1,00)** é importado por 6 dos 9 módulos (`classification.py:3`, `extraction.py:5`, `llm.py:6`, `report.py:3`, `suggestion.py:1`, `tickets.py:4`) e mistura coisas de naturezas diferentes:

- segredos dos providers (`config.py:3-4`) e URLs dos providers (`config.py:6-7`);
- nomes **físicos** de modelo por feature (`config.py:10-13`), que cada feature lê direto (`config.CLASSIFICATION_MODEL` etc.): trocar um modelo é editar fonte e reconstruir a imagem (Dor 4 do diagnóstico);
- limite de saída (`config.py:15`), arquivo de tickets e tamanho do lote (`config.py:17-18`).

**`schemas` (D = 1,00)** são os tipos pydantic do contrato HTTP (`schemas.py`, 7 classes `BaseModel`, sem comportamento nem dependência interna), importados por `main` e pelas 4 features (Ca = 5). O contrato externo é fixo (o enunciado e a suíte de caracterização o congelam), então a "dor" de mudá-los é a dor de mudar o contrato, que não vai acontecer. São candidatos a **estável por natureza**.

Fora da zona, mas relevantes:

- `tickets` está exatamente na fronteira (I = 0,50, D = 0,50): lê `config.TICKETS_FILE` (`tickets.py:9`) e é importado por `report`.
- As features têm D baixo (0,20–0,25) porque são instáveis (Ce alto) e ninguém além de `main` depende delas. A métrica não mostra o problema delas, que é **para onde** apontam as dependências: `config` e `llm`, dois módulos concretos e voláteis.

## Componentes-alvo e estratégia

| Componente | Problema (medido / no código) | Estratégia | Resultado esperado na métrica |
|---|---|---|---|
| `llm` → `ports.LanguageModel` + `adapters/gateway.py` | D = 0,80, Ca = 4, A = 0; conhece os dois SDKs e as chaves dos providers (`llm.py:3-9`); a feature escolhe o provider pelo nome da função | **Inverter a dependência** (e **aumentar A**): as features passam a depender de `ports.LanguageModel`, um `typing.Protocol` assíncrono (`async def complete(capability, task, content) -> str`). A implementação concreta `adapters/gateway.py` é o **único** componente que chama o gateway (SDK `openai` apontado para o LiteLLM, `max_retries=0`, timeout explícito, sempre `max_tokens`), recebe URL, chave e timeout pelo construtor (não importa `config`) e é importada só por `main`. `llm.py` é removido ao final | `llm` deixa de existir. `ports`: A = 1, Ce = 0 → D = 0 (abstrato e estável, na Main Sequence). `adapters.gateway`: Ca = 1 (só `main`), A = 0; para não cair na zona de dor precisa de Ce ≥ Ca, o que vem da dependência explícita no port que implementa → I ≥ 0,5. **Nunca pode ser exceção** |
| `classification`, `suggestion`, `extraction`, `report` | Ce = 3–4, todos concretos (`config`, `llm`, `tickets`); escolhem o modelo físico via `config.*_MODEL` | **Inverter a dependência** e **compor em vez de importar global**: recebem o `LanguageModel` (e, no `report`, a fonte de tickets) por injeção a partir de `main`; usam só o nome **lógico** da capacidade (`classify`, `suggest`, `topics`, `extract`); não importam `config` nem `llm` | Ce = {`ports`, `schemas`}, Ca = 1 (`main`) → I ≈ 0,67, A = 0, D ≈ 0,33: fora da zona. **Nunca podem ser exceção** |
| `config` | D = 1,00, Ca = 6; guarda chaves de provider e nomes físicos | **Reduzir Ca** e tirar responsabilidades: chaves de provider, URLs de provider e nomes físicos saem da app e vão para o gateway (`gateway/config.yaml` + ambiente do serviço `gateway`). Fica só o que varia por ambiente para a app (URL e chave virtual do gateway, timeouts, caminho dos tickets, tamanho do lote), lido **apenas** pelo ponto de composição | Ca = 1 (`main`), Ce = 0 → continua com D = 1. Declarado no ADR da métrica como estável por natureza (folha de constantes, um único leitor) |
| `tickets` → `adapters/tickets_file.py` | I = 0,50 (fronteira); lê `config` global | **Inverter a dependência**: `report` depende de `ports.TicketSource`; o adapter recebe o caminho pelo construtor e é composto por `main` | Ca = 1 (`main`), Ce = 1 (`ports`) → I = 0,5, D = 0,5: fora da zona, na fronteira |
| `schemas` | D = 1,00, Ca = 5; tipos de valor do contrato fixo | **Nenhuma mudança** (tipos pydantic concretos por natureza; torná-los abstratos não faz sentido) | Ca = 5, D = 1. Declarado no ADR da métrica como **estável por natureza** |
| `main` | Ce = 5, I = 1; só rotas | Vira o **ponto de composição** (compor em vez de importar global): lê `config`, constrói os adapters e os injeta nas features; único que importa `adapters.*` e `config` | I = 1, A = 0, D = 0 (Ce cresce, continua na Main Sequence) |

Decisões de desenho que valem para todas as features:

- **Nome lógico da capacidade**: a preferência inicial é uma constante na própria feature (ex.: `classification.CAPABILITY = "classify"`), e não um item de `config`. O nome lógico é parte do contrato estável app ↔ gateway (não varia por ambiente; o que varia, o modelo físico, mora no gateway), e colocá-lo em `config` obrigaria a feature a importar `config` de novo ou `main` a injetar mais um parâmetro por feature sem ganho. A confirmar no piloto.
- **Port assíncrono**: o `main` vai precisar de streaming (F2) e de lotes concorrentes (F3); com `async` desde o primeiro port evita-se retrabalho. A forma `complete(capability, task, content)` separa o roteamento (capacidade, que o gateway resolve) do protocolo da tarefa (`TASK: <task>` no prompt).
- **A app nunca envia política**: nada de `timeout`/`num_retries` no corpo nem headers `x-litellm-*`; retry e fallback são do gateway (fases seguintes). O timeout do cliente é só rede de segurança.

Sequência prevista: piloto (`llm` → port + adapter, migrando só a F1), depois F4, F2, F3 com `tickets` → adapter, limpeza de `config`, remoção de `llm.py` e do SDK `anthropic`, e por fim o serviço `app` do compose sem `env_file` (só URL e chave do gateway).

## Critério de pronto

A refatoração (tag `v2-decoupled`) está pronta quando **todos** valerem:

1. A suíte de caracterização, **inalterada** (`git diff v1-coupled -- tests/characterization/` vazio), passa: 20 testes verdes pela borda.
2. Nenhum componente na zona de dor (A < 0,5, I < 0,5, D ≥ 0,5), exceto os declarados no ADR da métrica como estáveis por natureza (previstos: `schemas` e `config`). O adapter do gateway e as features **nunca** podem ser exceção.
3. O código que chama o gateway está num único componente (`adapters.gateway`), importado apenas por `main`; `llm` não existe mais; nenhuma feature importa `adapters.*`, `config` ou `llm`.
4. Nenhum nome físico de modelo nem chave de provider em `app/`: `grep -rE "gpt-fake|claude-fake|FAKE_|sk-fake|sk-ant-fake" app/` vazio; o SDK `anthropic` sai de `app/requirements.txt`.
5. O serviço `app` não recebe as chaves dos providers: `docker compose exec app env` só mostra a URL e a chave virtual do gateway (sem `env_file: .env`).

## Piloto

**Componente**: `llm`. É o pior D fora dos tipos de valor (0,80) e é a raiz do acoplamento com os providers: é por causa dele que `config` carrega chaves e nomes físicos. Substituí-lo por port + adapter é o que destrava todas as outras mudanças. O piloto migra **uma** feature, a F1 (`classification`): saída curta (~1,1 s), determinística, e com uma regra de validação (502 em valores fora do permitido) que exercita o contrato do port. F2–F4 continuam usando `llm.py` (padrão estrangulamento), então a app ainda precisa das chaves dos providers durante o piloto.

**Passos**

1. Subir o gateway mínimo: `gateway` (LiteLLM) + `gateway-db` (Postgres) + provisionamento da chave virtual `helpdesk-app` com as 4 capacidades, cada uma apontando para o modelo **large** do seu provider; sem fallback nem política de retry ainda.
2. Criar `ports.py` com `class LanguageModel(Protocol)`.
3. Criar `adapters/gateway.py` (único que chama o gateway; configuração pelo construtor).
4. Migrar a F1: `classification` recebe o `LanguageModel` por parâmetro e usa o nome lógico `classify`; `main` passa a compor (constrói o adapter a partir de `config` e o injeta).
5. Compose: o `app` ganha `GATEWAY_BASE_URL` e `GATEWAY_API_KEY` e passa a depender do `gateway` saudável.
6. Rodar a caracterização; confirmar no log do gateway (modelo `classify`) e em `/admin/calls` (`gpt-fake-large`) que a F1 passa pelo gateway.
7. Medir com `metrics/coupling.py` e comparar com a v1.

**Medição antes/depois** (`python3 metrics/coupling.py app/helpdesk --name pilot`, executado em 2026-10-09; componentes afetados):

| Componente | v1: Ca / Ce / I / A / D | Piloto: Ca / Ce / I / A / D | Zona de dor |
|---|---|---|---|
| `llm` | 4 / 1 / 0,20 / 0,00 / **0,80** | 3 / 1 / 0,25 / 0,00 / **0,75** | sim → sim (ainda usado por F2–F4) |
| `classification` | 1 / 3 / 0,75 / 0,00 / 0,25 | 1 / 2 / 0,67 / 0,00 / 0,33 | não → não |
| `config` | 6 / 0 / 0,00 / 0,00 / **1,00** | 6 / 0 / 0,00 / 0,00 / **1,00** | sim → sim |
| `main` | 0 / 5 / 1,00 / 0,00 / 0,00 | 0 / 7 / 1,00 / 0,00 / 0,00 | não → não |
| `ports` (novo) | — | 2 / 0 / 0,00 / **1,00** / 0,00 | não |
| `adapters.gateway` (novo) | — | 1 / 1 / **0,50** / 0,00 / 0,50 | não (na fronteira I = 0,5) |
| `schemas` | 5 / 0 / 0,00 / 0,00 / **1,00** | 5 / 0 / 0,00 / 0,00 / **1,00** | sim → sim |

`extraction`, `suggestion`, `report` e `tickets` não mudaram. Zona de dor: v1 = {`config`, `llm`, `schemas`}; piloto = {`config`, `llm`, `schemas`}. Nenhum componente entrou na zona.

Leitura:

- `llm` perdeu um dependente (Ca 4 → 3, D 0,80 → 0,75): continua na zona porque F2–F4 ainda o usam; ele sai de cena na refatoração em escala, não antes.
- `ports` é o primeiro componente da base com abstração (A = 1). Ce = 0 e dois dependentes (`classification` e `adapters.gateway`) o colocam exatamente na Main Sequence (D = 0): é estável **e** abstrato, que é o lugar certo para aquilo de que todos dependem.
- `adapters.gateway` fica com Ca = 1 (`main`) e Ce = 1 (o port que implementa): I = 0,50, D = 0,50. Pelo critério (I < 0,5) está fora da zona, mas sem folga nenhuma (ver revisão R1).
- `classification` trocou duas dependências concretas e voláteis (`config`, `llm`) por uma abstrata (`ports`): Ce 3 → 2, e por isso o D **subiu** de 0,25 para 0,33. A métrica conta dependências, não olha para o que elas são; o ganho do piloto aparece em `ports` e na queda do Ca de `llm`, não no D da feature (ver R3).
- `config` não melhorou ainda: saiu um importador (`classification`) e entrou outro (`main`, o ponto de composição). Ca só cai quando F2–F4, `llm` e `tickets` deixarem de importá-lo.
- `main` ganhou `config` e `adapters.gateway` (Ce 5 → 7): é o papel de ponto de composição.

**Resultado dos testes**: `./tests/run.sh characterization` → **20 passed** (52,16 s; repetida no estado final do compose: 20 passed, 51,95 s), sem nenhuma mudança na suíte (`git diff v1-coupled -- tests/characterization/` vazio). A F1 passa pelo gateway: na execução da suíte, `/admin/calls` registrou 6 chamadas `task=classify` em `openai/gpt-fake-large`, o log de acesso do gateway registrou 6 `POST /v1/chat/completions` vindos do IP do `app`, e o log de gasto do gateway (`/spend/logs`) tem 6 linhas `model_group=classify` → `openai/gpt-fake-large` com custo calculado pelos preços declarados (ex.: 11 tokens de entrada e 12 de saída = US$ 0,0001475). `extract`, `suggest` e `topics` continuaram indo direto ao provider, como previsto. Com o provider OpenAI em `error_500`, a F1 responde `500 Internal Server Error`, como na v1.

## Revisões do plano

Achados do piloto e da medição (2026-10-09). Além da medição real acima, algumas variantes foram medidas em cópias descartáveis de `app/helpdesk/` (não versionadas) para quantificar as alternativas; cada número abaixo vem de uma execução de `metrics/coupling.py`.

**R1. O adapter fica exatamente na fronteira, e "importado só por `main`" vira requisito da métrica.** O plano previa I ≥ 0,5 para o adapter graças à dependência explícita no port; o piloto mostra que a margem é zero:

| Variante de `adapters.gateway` | Ca | Ce | I | D | Zona de dor |
|---|---|---|---|---|---|
| Como implementado: `class GatewayLanguageModel(LanguageModel)`, importado só por `main` | 1 | 1 | 0,50 | 0,50 | não |
| Conformidade só estrutural (sem importar o port, o que o `Protocol` permite) | 1 | 0 | 0,00 | 1,00 | **sim** |
| Um segundo importador (ex.: outra feature importando o adapter) | 2 | 1 | 0,33 | 0,67 | **sim** |

Mudanças: (a) todo adapter declara nominalmente o port que implementa (vale também para `adapters/tickets_file.py` com `TicketSource`); (b) o critério de pronto ganha um item explícito: **Ca = 1 (só `main`) para cada `adapters.*`**; (c) testes que importem adapters precisam ficar fora da pasta medida (hoje ficam em `tests/`, na raiz).

**R2. Exceções do port contam como classes concretas e podem jogar `ports` na zona de dor.** A fase de modos de execução vai precisar de erros próprios do port (ex.: 503 explícito na extração), e o plano previa "Protocols + exceções" em `ports.py`. Medido com `LanguageModel` + exceções:

| `ports.py` | A | D | Zona de dor |
|---|---|---|---|
| 1 Protocol (piloto) | 1,00 | 0,00 | não |
| 1 Protocol + 1 exceção | 0,50 | 0,50 | não (fronteira A = 0,5) |
| 2 Protocols (`LanguageModel`, `TicketSource`) + 3 exceções | 0,40 | 0,60 | **sim** |

Mudança: em `ports.py`, o número de classes de exceção não passa do número de `Protocol`s (uma exceção do port, ex. `LanguageModelUnavailable`, basta para o 503). Movê-las para um módulo próprio não resolve: pela própria fórmula, ele teria A = 0, Ce = 0 e vários importadores, ou seja I = 0 e D = 1.

**R3. D da feature não é meta; a regra de import é que vale.** Confirmado o número previsto (D ≈ 0,33), mas a medição mostra que a métrica premia o acoplamento errado: a variante em que `classification` importa `config` para ler o nome da capacidade dá Ce = 3, I = 0,75, **D = 0,25** (melhor que o piloto) e sobe o Ca de `config` para 7. O critério de pronto já proíbe features importando `config`/`llm`/`adapters.*` (item 3); ele passa a ser verificado por si só (grep/AST), e não inferido do D.

**R4. Nome lógico da capacidade: fica na feature (decisão que o plano deixou aberta).** `classification.CAPABILITY = "classify"`. Pôr em `config` piora o acoplamento medido (R3: Ca de `config` 6 → 7, uma dependência concreta a mais na feature); injetar por `main` não muda a métrica, mas acrescenta um parâmetro por feature para um valor que não varia por ambiente (quem varia é o modelo físico, no gateway). Na escala: cada feature declara a sua constante (`suggest`, `topics`, `extract`).

**R5. Forma do port: `async` confirmado, com duas consequências.** (a) A rota migrada vira `async def` e roda no event loop, enquanto as não migradas seguem `def` no pool de threads; as duas formas convivem (suíte verde), então a migração pode ser uma feature por vez. (b) O port ainda não traduz erros: a exceção do SDK (`openai.InternalServerError`) atravessa a feature até o FastAPI e vira 500, igual à v1. Isso basta para o contrato da v2; a exceção do port (R2) entra quando a fase de modos de execução precisar de 503.

**R6. Sem política, o gateway repete por conta própria.** Com a OpenAI em `error_500`, uma única chamada da app (que usa `max_retries=0`) gerou **3** chamadas `classify` em `/admin/calls` (00:45:46,75; 47,67; 48,94) e a resposta 500 saiu em 4,23 s: o roteador do LiteLLM usa 2 retentativas por padrão. O plano dizia "sem política de retry ainda", o que na prática significa "com a política padrão do LiteLLM". Mudança para a fase de resiliência: declarar `num_retries` e a `retry_policy` explicitamente e recontar as tentativas em `/admin/calls`.

**R7. O `app` sai do `env_file` já no piloto, e não no fim.** Com o gateway, o `.env` passou a conter a master key do gateway e a senha do banco; manter `env_file: .env` no `app` entregaria esses segredos à app. O serviço passou a receber só `GATEWAY_BASE_URL`, `GATEWAY_API_KEY` e, temporariamente, `FAKE_OPENAI_KEY`/`FAKE_ANTHROPIC_KEY` (para F2–F4 via `llm.py`). Na escala, basta apagar essas duas linhas. Pelo mesmo motivo, o `provider-fake` passou a receber só as duas chaves que valida.

Sem mudança: alvos, estratégias e sequência continuam os do plano. A projeção do estado final (port com `LanguageModel` e `TicketSource`, dois adapters importados só por `main`, features dependendo de `ports` + `schemas`), medida como esqueleto, deixa na zona de dor só `config` (Ca = 1) e `schemas` (Ca = 5), as duas exceções previstas. Fica espaço para revisões encontradas na refatoração em escala.

Achados da refatoração em escala (2026-10-09: F4, F2 e F3 no port, `tickets` → adapter, limpeza de `config`). Tokens contados pela regra do provider-fake (`ceil(caracteres / 4)`), que reproduz os 222.114 tokens de entrada do mês medidos no diagnóstico (34 lotes × 6.533).

**R8. Tamanho do lote: constante do `report`, e não item de `config`.** O plano deixava o tamanho do lote em `config`, entre "o que varia por ambiente". Medido, ele não varia por ambiente nem muda a resposta: é o quanto cabe no contexto do modelo que atende `topics` (8.000 tokens nos quatro modelos do provider-fake).

| Tickets por chamada | Lotes no mês de agosto | Entrada por lote, média / máx. (tokens) | Lotes acima de 8.000 | Relatório de 2026-08-01 (137 tickets, direto no provider) |
|---|---|---|---|---|
| 50 | 100 | 2.223 / 2.263 | 0 | 3 chamadas, igual ao golden |
| 100 | 50 | 4.443 / 4.496 | 0 | 2 chamadas, igual ao golden |
| **150** (v1) | 34 | 6.533 / 6.729 | 0 | 1 chamada, igual ao golden |
| 180 | 28 | 7.932 / 8.068 | **13** | — |
| 200 | 25 | 8.884 / 8.959 | **25** | — |

Decisão: `report.TICKETS_PER_CALL = 150`, ao lado de `report.CAPABILITY`, pelo mesmo motivo da R4: é parte do contrato lógico app ↔ gateway, não do ambiente. O outro lado do contrato fica escrito onde o modelo é trocado (`gateway/config.yaml`, comentário em `topics`: o modelo precisa de contexto ≥ 8.000 tokens). Injetar por `main` não mudaria a métrica (um `int` não é componente), mas criaria um parâmetro de ambiente para um valor que só muda junto com o modelo de `topics`. O lote segue sequencial; a fase de concorrência pode reduzi-lo (mais lotes, mesmo resultado, como mostra a tabela).

**R9. `TicketSource` também é assíncrono.** O plano não fixava a forma do segundo port. Com a rota da F3 virando `async def` (R5a), uma leitura síncrona do arquivo rodaria no event loop. Medido dentro do container `app` (20 leituras de `/data/tickets.jsonl`, 5.000 linhas): mediana 11,3 ms (um dia), 8,5 ms (mês) e 7,9 ms (período vazio), máximo 20,4 ms, tempo em que as F1, F2 e F4 em andamento ficariam paradas. Decisão: `async def in_period(start, end) -> list[dict]`, e `FileTicketSource` lê o arquivo em `asyncio.to_thread`. Sem custo na métrica: `ports` segue com A = 1 (dois `Protocol`s, nenhuma exceção, dentro do limite da R2).

**R10. Em falha: mesmo status e corpo, outro tempo; e o timeout do cliente muda o modo de falha da F2.** Provider em `error_500`, uma requisição por feature pela borda, antes (piloto: F2–F4 direto pelo SDK) e depois (v2: tudo pelo gateway):

| Feature (provider em falha) | Piloto | v2 | Tentativas em `/admin/calls` |
|---|---|---|---|
| F1 classify (openai) | 500 em 4,45 s | 500 em 4,11 s | 3 → 3 |
| F4 extract (openai) | 500 em 1,34 s | 500 em 5,23 s | 3 → 3 |
| F2 suggest (anthropic) | 500 em 1,45 s | 500 em 5,24 s | 3 → 3 |
| F3 topics, um dia (anthropic) | 500 em 1,39 s | 500 em 3,79 s | 3 → 3 |

Corpo `Internal Server Error` nos dois estados, e as features do outro provider seguem 200. É a R6 valendo para as quatro capacidades: as 2 retentativas saíram do SDK (agora com `max_retries=0`) e foram para o roteador do LiteLLM, com espera maior entre elas. Em operação normal o gateway não pesa (pela borda, antes → depois): extração 1,09 s → 1,09 s, sugestão 16,14 s → 16,15 s, relatório de um dia 9,84 s → 9,83 s.

Com a Anthropic em `timeout`, a F2 responde **500 aos 25,0 s** (`openai.APITimeoutError`, a rede de segurança `config.GATEWAY_TIMEOUT_SECONDS`), onde a v1 dava 504 da borda aos 30 s. A chamada segue aberta no provider depois que a app desistiu (`/admin/calls` ~35 s após o início: `suggest`, `status=504`, `duration_ms=null`): o gateway não cancela. A F2 normal leva 16,1 s, então sobram 8,9 s até a rede de segurança, e no modo `slow` (×10) ela falharia sempre. A suíte não cobre falhas e esta fase não muda política (R6), então nada disso foi corrigido aqui. Fica como entrada da fase de resiliência: timeout por capacidade no gateway (e streaming na F2), `num_retries` explícito e verificar se a chamada ao provider é cancelada quando a app desiste.

## Resultado da refatoração (v2-decoupled)

Estado final de `app/helpdesk/` (2026-10-09):

- `ports.py`: `LanguageModel` e `TicketSource`, dois `typing.Protocol` assíncronos (R9).
- `adapters/gateway.py` (`GatewayLanguageModel(LanguageModel)`) e `adapters/tickets_file.py` (`FileTicketSource(TicketSource)`, o antigo `tickets.py`): herança nominal do port (R1), configuração pelo construtor, importados só por `main`.
- Features `classification`, `extraction`, `suggestion` e `report`: rotas `async`, recebem o `LanguageModel` (e o `report`, o `TicketSource`) de `main` e declaram a capacidade lógica (`classify`, `extract`, `suggest`, `topics`, R4). Importam só `ports`, `schemas`, `fastapi`, `json` e `re`. O `report` continua sequencial e síncrono por contrato: um lote de 150 depois do outro, na mesma requisição (R8).
- `config.py`: URL e chave virtual do gateway (do ambiente), timeout do cliente, `MAX_OUTPUT_TOKENS` e caminho do arquivo de tickets. Lido só por `main`; sem chave, URL nem nome físico de provider.
- `llm.py` removido; `anthropic` fora de `app/requirements.txt` (o `openai` fica, usado só por `adapters/gateway.py`).
- Compose: o `app` recebe só `GATEWAY_BASE_URL` e `GATEWAY_API_KEY` e depende só do `gateway`. Saiu também o `depends_on` do `provider-fake`, com quem a app não fala mais.

### Métrica v1 → v2

`python3 metrics/coupling.py app/helpdesk --name v2-decoupled` → `metrics/results/v2-decoupled.csv` e `.svg` (medição oficial da v2). A v1 remedida a partir da tag (`git worktree add`) reproduz `metrics/results/v1-coupled.csv` byte a byte.

| Componente | v1: Ca / Ce / I / A / D | v2: Ca / Ce / I / A / D | Zona de dor v1 → v2 |
|---|---|---|---|
| `adapters.gateway` | — | 1 / 1 / 0,50 / 0,00 / 0,50 | — → não (fronteira I = 0,5, R1) |
| `adapters.tickets_file` (era `tickets`) | 1 / 1 / 0,50 / 0,00 / 0,50 | 1 / 1 / 0,50 / 0,00 / 0,50 | não → não (fronteira I = 0,5) |
| `classification` | 1 / 3 / 0,75 / 0,00 / 0,25 | 1 / 2 / 0,67 / 0,00 / 0,33 | não → não |
| `config` | 6 / 0 / 0,00 / 0,00 / **1,00** | 1 / 0 / 0,00 / 0,00 / **1,00** | sim → sim (estável por natureza) |
| `extraction` | 1 / 3 / 0,75 / 0,00 / 0,25 | 1 / 2 / 0,67 / 0,00 / 0,33 | não → não |
| `llm` | 4 / 1 / 0,20 / 0,00 / **0,80** | removido | sim → — |
| `main` | 0 / 5 / 1,00 / 0,00 / 0,00 | 0 / 8 / 1,00 / 0,00 / 0,00 | não → não |
| `ports` | — | 6 / 0 / 0,00 / **1,00** / 0,00 | — → não |
| `report` | 1 / 4 / 0,80 / 0,00 / 0,20 | 1 / 2 / 0,67 / 0,00 / 0,33 | não → não |
| `schemas` | 5 / 0 / 0,00 / 0,00 / **1,00** | 5 / 0 / 0,00 / 0,00 / **1,00** | sim → sim (estável por natureza) |
| `suggestion` | 1 / 3 / 0,75 / 0,00 / 0,25 | 1 / 2 / 0,67 / 0,00 / 0,33 | não → não |

**Zona de dor**: v1 = {`config`, `llm`, `schemas`}; piloto = {`config`, `llm`, `schemas`}; v2 = {`config`, `schemas`}, exatamente a projeção do esqueleto e as duas exceções previstas no plano.

Leitura:

- `llm` deixou de existir. O acoplamento com os providers mora num único componente, `adapters.gateway`, com Ca = 1, e os SDKs dos providers saíram da app.
- `config` segue com D = 1, mas o Ca caiu de 6 para 1: só o ponto de composição o lê, e o conteúdo deixou de ser segredo de provider e nome físico de modelo.
- `ports` é o componente mais usado (Ca = 6) e o único abstrato (A = 1, D = 0): as dependências das features e dos adapters convergem para uma abstração estável, na Main Sequence.
- As quatro features ficaram iguais (Ca 1, Ce 2 = {`ports`, `schemas`}, D = 0,33). O D subiu de 0,20–0,25 para 0,33, como previsto na R3: não é meta.
- A métrica não mostra a direção das dependências, então ela foi contada com o mesmo parser do `coupling.py`. Arestas de feature para módulo concreto e volátil (`config`, `llm`, `tickets`, `adapters.*`): v1 = 9, piloto = 7, v2 = **0**. Arestas para `ports`: 0 → 2 → 6. Importadores de `config`: 6 → 6 → 1 (`main`).

### Critério de pronto

| # | Critério | Evidência (comando → resultado) | |
|---|---|---|---|
| 1 | Suíte inalterada e verde | `git diff --stat v1-coupled -- tests/characterization/` → vazio; `./tests/run.sh characterization` → `20 passed in 52.07s`; repetida no estado final, depois de um novo `docker compose up -d --build --wait`: `20 passed in 51.98s` | ok |
| 2 | Zona de dor só com os estáveis por natureza; adapter e features fora | `python3 metrics/coupling.py app/helpdesk --name v2-decoupled` → `Zona de dor (A<0.5, I<0.5, D>=0.5): config, schemas`; `adapters.gateway` I = 0,50, features D = 0,33 | ok: `config` e `schemas` declarados estáveis por natureza, com justificativa, no [ADR 0010](adr/0010-leitura-da-metrica-de-acoplamento.md) (escrito antes da tag, por exigência do orquestrador) |
| 2b | Ca = 1 para cada `adapters.*` (R1) | `metrics/results/v2-decoupled.csv` → `adapters.gateway,1,1,…` e `adapters.tickets_file,1,1,…` | ok |
| 3 | Chamada ao gateway num só componente, importado só por `main`; sem `llm`; nenhuma feature importa `adapters.*`/`config`/`llm` | `grep -rln openai app/helpdesk` → só `adapters/gateway.py`; `grep -rnE "^\s*(from\|import) .*(adapters\|config)" app/helpdesk` → só `main.py` (3 linhas); `grep -nE "^\s*(from\|import) " app/helpdesk/{classification,extraction,suggestion,report}.py` → só `.ports`, `.schemas`, `fastapi`, `json`, `re`; `app/helpdesk/llm.py` não existe | ok |
| 4 | Nenhum nome físico nem chave de provider em `app/`; sem SDK `anthropic` | `grep -rE "gpt-fake\|claude-fake\|FAKE_\|sk-fake\|sk-ant-fake\|anthropic" app/` → vazio (exit 1); na imagem, `importlib.util.find_spec('anthropic')` → `None` (o `openai` continua instalado) | ok |
| 5 | O `app` não recebe as chaves dos providers | `docker compose exec app env \| grep -iE "fake\|anthropic\|openai_api"` → vazio; variáveis do container: `GATEWAY_API_KEY`, `GATEWAY_BASE_URL` e as da imagem `python` (`GPG_KEY`, `HOME`, `HOSTNAME`, `LANG`, `PATH`, `PYTHON_SHA256`, `PYTHON_VERSION`); `docker compose config app` → `environment` só com as duas, sem `env_file`, único volume `./data:/data:ro` | ok |

Evidência complementar:

- **As 4 capacidades passam pelo gateway.** Depois da suíte, `/admin/calls`: `classify` 6 e `extract` 3 em `openai/gpt-fake-large`, `suggest` 2 e `topics` 1 em `anthropic/claude-fake-large`, todas `200`. No gateway, as 12 últimas linhas de `/spend/logs` têm `model_group` `classify` 6 e `extract` 3 → `openai/gpt-fake-large`, `suggest` 2 e `topics` 1 → `anthropic/claude-fake-large` (US$ 0,0438 no total), e o log de acesso tem 12 `POST /v1/chat/completions` vindos do IP do `app`.
- **A app não envia política.** `grep -rnE "num_retries|x-litellm|extra_headers|extra_body" app/helpdesk` só acha a docstring do adapter. O cliente usa `max_retries=0` e o `timeout` do construtor do SDK, que é local e não vai para o gateway.
- **Regras de validação preservadas.** Com um `LanguageModel` falso injetado (dentro do container): classificação fora dos valores → `502 'Classificação fora dos valores permitidos'`; pedido `"123"` → `502 'Número de pedido em formato inválido'`; `start > end` → `422 'A data inicial é posterior à data final'`; 301 tickets → 3 chamadas `topics` em sequência. A suíte cobre os 422 pela borda.
- **Falhas e latência**: ver R10. O relatório mensal continua estourando os 30 s da borda (Dor 1), pois sequencial e síncrono são o contrato desta tag; concorrência e request-reply assíncrono são a próxima fase.

## Handoff para o agente de IA

**Quem fez o quê.** A refatoração foi feita pelo Claude Code em dois papéis. Uma sessão orquestradora (Claude Opus 5.5) decidiu a arquitetura, escreveu os briefs e julgou as entregas. Ela não escreveu código de produção. A execução ficou com subagentes `general-purpose` do Claude Code, cada um numa sessão nova, sem o histórico das fases anteriores: Opus 5.5 no piloto (Fase 4a) e na escala (Fase 4b), Sonnet 5.5 no script de métrica e na suíte de caracterização. Os agentes não podiam fazer commit nem tag. Só o orquestrador fazia, e só depois dos juízes.

**O que foi entregue ao agente da escala (Fase 4b)**

1. Este documento como especificação: plano original, piloto e revisões R1–R7, com o estado de partida no commit `0874670` (F1 já pelo gateway, F2–F4 ainda via `llm.py`).
2. Escopo:
   - migrar F4, F2 e F3 para `ports.LanguageModel`;
   - transformar `tickets` em `adapters/tickets_file.py` com `ports.TicketSource`;
   - tirar de `config` chaves, URLs de provider e nomes físicos;
   - remover `llm.py` e o SDK `anthropic`;
   - tirar `FAKE_*` do serviço `app`;
   - manter o comportamento externo: rotas e respostas síncronas, F3 sequencial.
3. Proibições:
   - não tocar `tests/characterization/`, `metrics/coupling.py`, `provider-fake/`, `edge/` e `data/`;
   - não mudar o comportamento externo;
   - não enviar política do gateway pela app (`timeout`, `num_retries`, `x-litellm-*`);
   - não fazer commit.
4. Os juízes, todos determinísticos e com o resultado esperado declarado antes:
   - `./tests/run.sh characterization` → 20 passed, com `git diff v1-coupled -- tests/characterization/` vazio;
   - `python3 metrics/coupling.py app/helpdesk --name v2-decoupled` → zona de dor só com {`config`, `schemas`};
   - o grep de nomes físicos e chaves;
   - imports das features só para `ports` e `schemas`;
   - `adapters.*` com Ca = 1;
   - ambiente do `app` sem chaves de provider.

**Por que o agente não podia "passar no juiz" trapaceando**

- A suíte e o script de métrica estavam congelados por `git diff` contra a tag e contra o commit do script.
- A tabela da v1 foi calculada à mão pelo orquestrador antes de existir o script, e o script só foi aceito quando reproduziu essa tabela por `diff`.
- O orquestrador não confiou no relatório do agente: re-executou cada juiz no próprio terminal antes de aceitar.

**Como os juízes aceitaram ou rejeitaram o que o agente produziu**

| Entrega | Juízes re-executados pelo orquestrador | Decisão |
|---|---|---|
| Script de métrica (Sonnet) | `pytest -q metrics/tests` → 8 passed; `diff` do CSV com a tabela à mão → idêntico | Aceito. O gráfico foi **rejeitado** na revisão visual (rótulos sobrepostos, legenda da zona sobre `config`) e devolvido para correção. O CSV não podia mudar: `diff` vazio de novo |
| Piloto (Opus) | `git diff` da suíte vazio; caracterização 20 passed (51,94 s); métrica recalculada, com zona de dor {config, llm, schemas} igual à relatada; leitura do código (adapter único, port `Protocol`, feature sem `config`/`llm`) | Aceito com uma ressalva: a herança nominal do port (R1a) só fica se for justificada por desenho, não pela fórmula. Isso virou exigência do ADR da métrica |
| Escala (Opus) | `git diff` da suíte e do script vazios; caracterização 20 passed (52,01 s); métrica com zona de dor {config, schemas} e CSV versionado igual ao recalculado; grep vazio; só `main` importa `config`/`adapters`; `docker compose exec app env` só com `GATEWAY_*`; `docker compose config app` sem `env_file` | Aceito. O próprio agente marcou o critério 2 como pendente (faltava o ADR que declara as exceções). O orquestrador **segurou a tag** `v2-decoupled` até o ADR 0010 existir |

**O que o agente decidiu sozinho e foi aceito porque veio com medição**

- R8: tamanho do lote como constante do `report`. Medição: tokens por lote contra a janela de 8k.
- R9: `TicketSource` assíncrono. Medição: tempo de leitura do arquivo no event loop.

O agente também apontou que, em falha, a v2 responde mais devagar e de outro jeito (R10). Isso não foi "consertado" na v2, de propósito: virou entrada da fase de resiliência, porque a tag v2 não podia mudar comportamento.
