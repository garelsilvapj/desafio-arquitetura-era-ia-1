#!/bin/bash
# Entrypoint do gateway: provisiona as chaves virtuais em segundo plano e entrega o
# processo principal ao LiteLLM. O healthcheck do compose só fica verde quando o
# LiteLLM está pronto E as chaves existem (/tmp/keys.ok).
set -euo pipefail

rm -f /tmp/keys.ok
# Subshell: o provisionador fica órfão e é adotado pelo init do container (init: true),
# que o recolhe ao terminar; o exec abaixo não precisa esperar por ele.
( python3 /config/provision_keys.py & )

exec litellm "$@"
