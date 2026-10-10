from fastapi import FastAPI

from . import classification, config, extraction, report, suggestion
from .adapters.gateway import GatewayLanguageModel
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)

# Ponto de composição: único lugar que lê a configuração e conhece o adapter concreto do
# gateway; as features recebem o modelo pelo port (ports.LanguageModel).
language_model = GatewayLanguageModel(
    base_url=config.GATEWAY_BASE_URL,
    api_key=config.GATEWAY_API_KEY,
    timeout=config.GATEWAY_TIMEOUT_SECONDS,
    max_tokens=config.MAX_OUTPUT_TOKENS,
)

app = FastAPI(title="Helpdesk")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
async def classify_ticket(ticket: TicketInput) -> Classification:
    return await classification.classify(ticket, language_model)


@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> ReplySuggestion:
    return suggestion.suggest(ticket)


@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extraction.extract(ticket)


@app.post("/reports/topics")
def topics_report(period: PeriodInput) -> TopicsReport:
    return report.topics(period)
