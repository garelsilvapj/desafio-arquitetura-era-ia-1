#!/usr/bin/env bash
# Uso: ./tests/run.sh <suite> [args extras do pytest]
#   ex.: ./tests/run.sh characterization
# Variáveis: BASE_URL (padrão http://localhost:8000), PROVIDER_URL (padrão http://localhost:8090).
set -eu

if [ $# -lt 1 ]; then
  echo "uso: $0 <suite> [args do pytest]" >&2
  exit 2
fi
suite=$1
shift

root=$(cd "$(dirname "$0")/.." && pwd)
venv="$root/.venv"
req="$root/tests/requirements.txt"
stamp="$venv/.requirements.sha"

if [ ! -d "$root/tests/$suite" ]; then
  echo "suíte inexistente: tests/$suite" >&2
  exit 2
fi

[ -x "$venv/bin/python" ] || python3 -m venv "$venv"
current=$(cksum < "$req")
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current" ]; then
  "$venv/bin/python" -m pip install -q --disable-pip-version-check -r "$req"
  echo "$current" > "$stamp"
fi

export BASE_URL="${BASE_URL:-http://localhost:8000}"
export PROVIDER_URL="${PROVIDER_URL:-http://localhost:8090}"
cd "$root"
exec "$venv/bin/python" -m pytest "tests/$suite" -q -p no:cacheprovider "$@"
