# 0008. Guardar chaves e gasto do gateway num Postgres próprio, com volume nomeado

- Status: aceita
- Data: 2026-10-09
- Nível: solução

## Contexto

O desafio exige governança no gateway: chaves com orçamento e com limite de requisições. O teto corporativo de US$ 80 a cada 30 dias ([ADR 0001](0001-politica-de-providers-e-teto-de-gasto.md)) é o orçamento da chave `helpdesk-app` ([ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md)). No LiteLLM Proxy v1.102.1 ([ADR 0002](0002-ai-gateway-self-hosted.md)), chave virtual, orçamento, limite e gasto acumulado só existem com banco:

- sem `DATABASE_URL`, `/key/generate` responde 500 com `DB not connected. This endpoint needs a database; set DATABASE_URL to a PostgreSQL connection string (postgresql://...)` (`CommonProxyErrors.db_not_connected_error` em `litellm/proxy/_types.py`, levantado em `key_management_endpoints.py`);
- o esquema é Prisma com `provider = "postgresql"` (`litellm/proxy/schema.prisma`): não há SQLite nem outro banco;
- na subida, o gateway aplica 171 migrações (`prisma migrate deploy`, ~2 s).

Além disso, o LiteLLM não aceita chaves declaradas no `config.yaml`: elas são criadas pela API de admin. Algo precisa criá-las em todo ambiente novo e reconciliá-las quando o `.env` muda.

## Opções consideradas

Banco:

1. **Sem banco, só a master key.** A app usaria a master key (poder de admin dentro da app), sem orçamento, sem limite e sem lista de modelos por chave. Não atende o requisito.
2. **SQLite dentro do container do gateway.** O esquema do proxy não suporta.
3. **Postgres gerenciado, fora do compose.** É o natural em produção, mas o ambiente do desafio precisa subir inteiro com `docker compose up`.
4. **Postgres como serviço do compose, dedicado ao gateway.**

Provisionamento das chaves:

5. **Serviço `gateway-init` de execução única.** Espera o gateway `healthy`, cria as chaves e sai. A app passaria a depender dele com `service_completed_successfully`.
6. **Dentro do container do gateway**, disparado pelo entrypoint.

## Decisão

Opções 4 e 6.

**`gateway-db`.** Imagem `postgres:17-alpine`, usuário e banco `litellm`, senha `GATEWAY_DB_PASSWORD` obrigatória no `.env` (`:?`), healthcheck `pg_isready` (a cada 2 s, até 30 tentativas). O `gateway` só sobe com o banco `healthy`, para as migrações não disputarem a subida do Postgres. A porta do banco não é publicada no host, e a senha só chega ao `gateway-db` e ao `gateway` (o `app` não tem `env_file`, R7 do [plano](../refactoring-plan.md)).

**Volume nomeado `gateway-db-data`.** Chaves e gasto acumulado sobrevivem a `restart`, `down` e `up`, porque um orçamento que zera a cada reinício não é orçamento. `docker compose down -v` é o comando de "zerar tudo": apaga chaves e gasto, e o próximo `up` recria a chave.

**Provisionamento no próprio gateway, não num serviço a mais.** `gateway/entrypoint.sh` apaga `/tmp/keys.ok`, dispara `gateway/provision_keys.py` em segundo plano e faz `exec litellm`. O provisionador fica órfão e é recolhido pelo `init: true`. Ele espera o `/health/readiness` e, para cada chave declarada na lista `KEYS`, consulta `/key/list?key_alias=…`:

- chave com o mesmo hash: `/key/update`, que preserva o gasto;
- chave ausente: `/key/generate`;
- alias com outro hash (o valor mudou no `.env`): `/key/delete` + `/key/generate`, uma rotação.

Ao terminar, cria `/tmp/keys.ok`. Como o healthcheck exige `/health/readiness` **e** `/tmp/keys.ok`, gateway `healthy` passa a significar "chaves existem". O `depends_on` da app e o `up --wait` continuam simples.

Um `gateway-init` seria mais um serviço, com a imagem de 1,19 GB, e mais uma dependência na app, e não rodaria de novo num `restart gateway`. No desenho escolhido, a reconciliação roda a cada start do gateway.

## Consequências

- **Melhor:**
  - Orçamento, limite e lista de modelos por chave ficam possíveis ([ADR 0007](0007-governanca-chaves-orcamentos-e-limites.md)).
  - O gasto fica registrado por chamada (`/spend/logs`, com `model_group`, modelo físico e custo) e acumulado por chave (`spend`).
  - Ambiente novo sobe sem passo manual: `cp .env.example .env && docker compose up -d --build --wait`.
- **Pior / custo:**
  - Mais um serviço com estado para operar: backup, espaço e versão do Postgres.
  - O banco entra na disponibilidade das features de IA, porque o gateway depende dele para autenticar chaves e registrar gasto. O comportamento com o banco fora do ar não foi medido.
  - A subida fica mais lenta:
    - a frio (`down -v` + `up --build --wait`), 27,03 s e 26,86 s em duas medições;
    - o gateway leva ~15 s do start ao `healthy` (provisionador pronto aos 12,0 s);
    - um `restart gateway` leva 11,3 s até `healthy` (provisionador em 7,1 s).
- **Vigiar:**
  - `down -v` e a rotação do valor da chave zeram o gasto contado. Num ambiente real, isso libera até mais um teto inteiro na mesma janela.
  - Os logs de gasto crescem uma linha por chamada: ~15.100 por mês no volume de agosto (3 × 5.000 + 4 × 34).
  - Chave nova, orçamento ou limite são dados na lista `KEYS` de `gateway/provision_keys.py`, aplicados no próximo start do gateway.

## Evidência

```bash
docker compose config gateway-db     # postgres:17-alpine, volume gateway-db-data, sem ports
docker compose ps gateway-db gateway  # healthy
# o banco é requisito do LiteLLM (fonte dentro da imagem)
docker compose exec gateway bash -c 'd=$(python3 -c "import litellm, os; print(os.path.dirname(litellm.__file__))")
  grep -A3 "db_not_connected_error = (" $d/proxy/_types.py; grep -m1 -A1 "^datasource" $d/proxy/schema.prisma'
# restart preserva chave e gasto
docker compose restart gateway && docker compose logs gateway | grep provision_keys | tail -2
# zerar tudo e subir a frio
docker compose down -v && time docker compose up -d --build --wait
docker compose logs gateway | grep -E "migrations|provision_keys"
```

Saídas observadas:

```text
# restart (/key/update)
[provision_keys] chave helpdesk-app: atualizada (gasto preservado: US$ 0.000315) (models=['classify', 'suggest', 'topics', 'extract'])
[provision_keys] 1 chave(s) provisionada(s) em 7.1s; /tmp/keys.ok criado
# down -v + up: volume removido, 171 migrações, chave recriada; 26,86 s no total, 0 linhas ERROR/Traceback
 Volume desafio-arquitetura-era-ia-1_gateway-db-data  Removed
171 migrations found in prisma/migrations
[provision_keys] chave helpdesk-app: criada (models=['classify', 'suggest', 'topics', 'extract'])
[provision_keys] 1 chave(s) provisionada(s) em 12.1s; /tmp/keys.ok criado
# rotação: GATEWAY_APP_KEY=sk-helpdesk-app-0002 docker compose up -d --wait gateway
[provision_keys] helpdesk-app: alias usado por outra chave; removendo a antiga (rotação)
key 0001 -> 401 / key 0002 -> 200
```
