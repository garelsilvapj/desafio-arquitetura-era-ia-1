import os

OPENAI_API_KEY = os.environ["FAKE_OPENAI_KEY"]
ANTHROPIC_API_KEY = os.environ["FAKE_ANTHROPIC_KEY"]

OPENAI_BASE_URL = "http://provider-fake:8090/openai/v1"
ANTHROPIC_BASE_URL = "http://provider-fake:8090/anthropic"

# Ainda chamados direto (llm.py) até migrarem para o gateway: extração na OpenAI;
# sugestão e relatório na Anthropic. A classificação já passa pelo gateway.
EXTRACTION_MODEL = "gpt-fake-large"
SUGGESTION_MODEL = "claude-fake-large"
REPORT_MODEL = "claude-fake-large"

MAX_OUTPUT_TOKENS = 2000

# AI Gateway: a app conhece só a URL e a sua chave virtual; o modelo físico de cada
# capacidade é escolhido no gateway (gateway/config.yaml).
GATEWAY_BASE_URL = os.environ["GATEWAY_BASE_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Rede de segurança do cliente, abaixo dos 30 s da borda; retry e fallback são do gateway.
GATEWAY_TIMEOUT_SECONDS = 25.0

TICKETS_FILE = "/data/tickets.jsonl"
TICKETS_PER_CALL = 150
