import os

# Lido só pelo ponto de composição (main). Chaves, URLs e modelos físicos dos providers
# não estão na app: moram no gateway (gateway/config.yaml e o ambiente do serviço gateway).

# AI Gateway: a app conhece só a URL e a sua chave virtual; o modelo físico de cada
# capacidade é escolhido no gateway.
GATEWAY_BASE_URL = os.environ["GATEWAY_BASE_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Rede de segurança do socket do cliente; retry e fallback são do gateway. Os prazos que
# valem são das features (F1/F4 13 s, F2 20 s até o 1º trecho e 10 s entre trechos, F3
# 50 s por lote); este fica acima do maior deles para nunca cortar um lote antes do prazo.
GATEWAY_TIMEOUT_SECONDS = 55.0
MAX_OUTPUT_TOKENS = 2000

TICKETS_FILE = "/data/tickets.jsonl"

# Relatório de tópicos (F3): lotes em paralelo por job (o mês, 34 lotes, cai de ~330 s em
# sequência para ~90 s) e quantos jobs ficam na memória para consulta.
TOPICS_CONCURRENT_BATCHES = 4
TOPICS_JOBS_KEPT = 100
