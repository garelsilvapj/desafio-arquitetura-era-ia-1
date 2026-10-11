#!/usr/bin/env bash
# Roteiro de governança do AI Gateway: orçamento e limite de taxa por chave virtual.
#
# Uso (com a stack no ar: cp .env.example .env && docker compose up -d --build --wait):
#   ./scripts/governance-demo.sh
#
# O que demonstra, chamando a capacidade `classify` direto no gateway (localhost:4000):
#   1. Orçamento (chave demo-budget, teto US$ 0,000005): zera o gasto da chave, faz 2
#      chamadas. A 1ª passa (200); a 2ª é recusada pelo gateway (429, budget_exceeded).
#   2. Limite de taxa (chave demo-rpm, rpm_limit 2): 3 chamadas seguidas. As 2 primeiras
#      passam; a 3ª é recusada (429, throttling_error).
#   Em cada parte, conta as chamadas que chegaram ao provider (GET /admin/calls do
#   provider-fake): só as aceitas chegam (+1 e +2). A recusa acontece no gateway, antes
#   de qualquer chamada ao provider, então não gasta nada.
#
# Repetível: o gasto da demo-budget é zerado no início (POST /key/<hash>/reset_spend, com
# a master key). A janela do rpm é de 60 s a partir da 1ª chamada; se uma execução
# anterior deixou a janela aberta, o roteiro espera ela fechar (até ~60 s) e segue.
# Não mexe nos modos de falha do provider nem limpa o registro de chamadas dele.
#
# Variáveis (padrão: lidas do .env da raiz): LITELLM_MASTER_KEY, GATEWAY_DEMO_BUDGET_KEY,
# GATEWAY_DEMO_RPM_KEY; GATEWAY_URL (http://localhost:4000), PROVIDER_URL
# (http://localhost:8090). Requer bash, curl e python3.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
env_file=${ENV_FILE:-$root/.env}

read_env() {  # valor de uma variável: ambiente primeiro, depois o .env (sem executá-lo)
  local name=$1
  if [ -n "${!name:-}" ]; then
    printf '%s' "${!name}"
  elif [ -f "$env_file" ]; then
    grep -E "^${name}=" "$env_file" | tail -n 1 | cut -d= -f2-
  fi
}

GATEWAY_URL=${GATEWAY_URL:-http://localhost:4000}
PROVIDER_URL=${PROVIDER_URL:-http://localhost:8090}
MASTER_KEY=$(read_env LITELLM_MASTER_KEY)
BUDGET_KEY=$(read_env GATEWAY_DEMO_BUDGET_KEY)
RPM_KEY=$(read_env GATEWAY_DEMO_RPM_KEY)
for pair in "LITELLM_MASTER_KEY:$MASTER_KEY" "GATEWAY_DEMO_BUDGET_KEY:$BUDGET_KEY" "GATEWAY_DEMO_RPM_KEY:$RPM_KEY"; do
  if [ -z "${pair#*:}" ]; then
    echo "ERRO: ${pair%%:*} não definida (nem no ambiente, nem em $env_file)" >&2
    exit 2
  fi
done

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Corpo fixo: uma classificação curta (custa US$ 0,0001575 no gpt-fake-large).
BODY='{"model":"classify","max_tokens":2000,"messages":[{"role":"user","content":"TASK: classify\nMeu pedido #481516 não chegou e já passou do prazo, urgente"}]}'

failures=0
results=()
check() {  # check <descrição> <esperado> <obtido>
  if [ "$2" = "$3" ]; then
    results+=("PASS  $1 (esperado $2, obtido $3)")
  else
    results+=("FAIL  $1 (esperado $2, obtido $3)")
    failures=$((failures + 1))
  fi
}

json_get() {  # json_get <arquivo> <expressão python sobre d>; vazio se não for JSON
  python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(eval(sys.argv[2]))
except Exception:
    print("")
' "$1" "$2"
}

# Chamadas ao provider: ids das últimas 50 (o registro guarda as últimas 5.000).
snapshot() { curl -sf "$PROVIDER_URL/admin/calls?last=50" > "$tmp/calls_$1.json"; }
new_calls() {  # new_calls <antes> <depois>: quantas chamadas novas entre os dois retratos
  python3 -c '
import json, sys
before = {c["id"] for c in json.load(open(sys.argv[1]))}
after = json.load(open(sys.argv[2]))
new = [c for c in after if c["id"] not in before]
for c in reversed(new):
    print("    provider: {ts} {provider}/{model} task={task} status={status}".format(**c), file=sys.stderr)
print(len(new))
' "$tmp/calls_$1.json" "$tmp/calls_$2.json"
}

classify() {  # classify <chave> <rótulo>: grava corpo/headers em $tmp/<rótulo>.*, devolve o status
  curl -s -o "$tmp/$2.body" -D "$tmp/$2.headers" -w '%{http_code}' \
    "$GATEWAY_URL/v1/chat/completions" \
    -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -d "$BODY"
}

describe() {  # describe <rótulo> <status>: uma linha legível sobre a resposta
  local cost
  if [ "$2" = 200 ]; then
    cost=$(grep -i '^x-litellm-response-cost:' "$tmp/$1.headers" | tr -d '\r' | awk '{print $2}')
    echo "    -> 200 $(json_get "$tmp/$1.body" 'd["choices"][0]["message"]["content"]')  custo US\$ ${cost:-?}"
  else
    echo "    -> $2 $(json_get "$tmp/$1.body" 'd["error"]["type"] + ": " + d["error"]["message"]')"
  fi
}

echo "== Pré-checagem"
curl -sf "$GATEWAY_URL/health/readiness" > /dev/null || { echo "ERRO: gateway fora do ar em $GATEWAY_URL" >&2; exit 2; }
curl -sf "$PROVIDER_URL/health" > /dev/null || { echo "ERRO: provider-fake fora do ar em $PROVIDER_URL" >&2; exit 2; }
modes=$(curl -sf "$PROVIDER_URL/admin/failures")
if ! python3 -c 'import json,sys; m=json.loads(sys.argv[1]); sys.exit(0 if set(m["effective_by_model"].values()) <= {"normal"} else 1)' "$modes"; then
  echo "AVISO: o provider não está em modo normal ($modes)."
  echo "       As contagens podem divergir; para limpar: curl -X POST $PROVIDER_URL/admin/reset"
fi
echo "   gateway e provider no ar"

echo
echo "== 1. Orçamento: chave demo-budget"
budget_hash=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$BUDGET_KEY")
curl -s -o "$tmp/reset.json" -w '%{http_code}' -X POST "$GATEWAY_URL/key/$budget_hash/reset_spend" \
  -H "Authorization: Bearer $MASTER_KEY" -H 'Content-Type: application/json' -d '{"reset_to": 0}' > "$tmp/reset.status"
if [ "$(cat "$tmp/reset.status")" != 200 ]; then
  echo "ERRO: não consegui zerar o gasto da demo-budget: $(cat "$tmp/reset.status") $(cat "$tmp/reset.json")" >&2
  exit 1
fi
echo "   gasto zerado (antes: US\$ $(json_get "$tmp/reset.json" '"%.7f" % d["previous_spend"]'); teto: US\$ $(json_get "$tmp/reset.json" '"%.6f" % d["max_budget"]'))"
snapshot b0
s1=$(classify "$BUDGET_KEY" b1); echo "   chamada 1"; describe b1 "$s1"
s2=$(classify "$BUDGET_KEY" b2); echo "   chamada 2"; describe b2 "$s2"
sleep 1
snapshot b1
nb=$(new_calls b0 b1)
echo "   chamadas novas no provider: $nb"
check "orçamento: 1ª chamada aceita" 200 "$s1"
check "orçamento: 2ª chamada recusada pelo gateway" "429 budget_exceeded" "$s2 $(json_get "$tmp/b2.body" 'd["error"]["type"]')"
check "orçamento: chamadas que chegaram ao provider" 1 "$nb"

echo
echo "== 2. Limite de taxa: chave demo-rpm (2 por minuto)"
for attempt in 1 2; do
  snapshot r0
  r1=$(classify "$RPM_KEY" r1)
  if [ "$r1" = 429 ] && [ "$attempt" = 1 ]; then
    # Janela aberta por uma execução anterior: espera ela fechar e recomeça a parte 2.
    wait_s=$(python3 -c '
import datetime as dt, json, re, sys
m = re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", json.load(open(sys.argv[1]))["error"]["message"])
if not m:
    print(61); sys.exit()
reset = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
print(max(1, int((reset - dt.datetime.now(dt.timezone.utc)).total_seconds()) + 2))
' "$tmp/r1.body")
    echo "   janela de 60 s ainda aberta por uma execução anterior (recusa sem chamada ao provider);"
    echo "   aguardando ${wait_s}s para recomeçar"
    sleep "$wait_s"
    continue
  fi
  r2=$(classify "$RPM_KEY" r2)
  r3=$(classify "$RPM_KEY" r3)
  break
done
for v in r1 r2 r3; do echo "   chamada ${v#r}"; describe "$v" "${!v}"; done
sleep 1
snapshot r1
nr=$(new_calls r0 r1)
echo "   chamadas novas no provider: $nr"
check "rpm: 1ª chamada aceita" 200 "$r1"
check "rpm: 2ª chamada aceita" 200 "$r2"
check "rpm: 3ª chamada recusada pelo gateway" "429 throttling_error" "$r3 $(json_get "$tmp/r3.body" 'd["error"]["type"]')"
check "rpm: chamadas que chegaram ao provider" 2 "$nr"

echo
echo "== Resumo"
for line in "${results[@]}"; do echo "   $line"; done
if [ "$failures" -eq 0 ]; then
  echo "PASS: orçamento e limite de taxa recusados no gateway, sem chamada ao provider"
  exit 0
fi
echo "FAIL: $failures verificação(ões) falharam"
exit 1
