import os

# Lido só pelo ponto de composição (main). Chaves, URLs e modelos físicos dos providers
# não estão na app: moram no gateway (gateway/config.yaml e o ambiente do serviço gateway).

# AI Gateway: a app conhece só a URL e a sua chave virtual; o modelo físico de cada
# capacidade é escolhido no gateway.
GATEWAY_BASE_URL = os.environ["GATEWAY_BASE_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Rede de segurança do cliente, abaixo dos 30 s da borda; retry e fallback são do gateway.
GATEWAY_TIMEOUT_SECONDS = 25.0
MAX_OUTPUT_TOKENS = 2000

TICKETS_FILE = "/data/tickets.jsonl"
