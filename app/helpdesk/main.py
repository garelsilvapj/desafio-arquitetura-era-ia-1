from fastapi import FastAPI

from . import classification, config, extraction, report, suggestion
from .adapters.gateway import GatewayLanguageModel
from .adapters.tickets_file import FileTicketSource
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)

# Ponto de composição: único lugar que lê a configuração e conhece os adapters concretos;
# as features recebem as dependências pelos ports (ports.LanguageModel, ports.TicketSource).
language_model = GatewayLanguageModel(
    base_url=config.GATEWAY_BASE_URL,
    api_key=config.GATEWAY_API_KEY,
    timeout=config.GATEWAY_TIMEOUT_SECONDS,
    max_tokens=config.MAX_OUTPUT_TOKENS,
)
ticket_source = FileTicketSource(config.TICKETS_FILE)

app = FastAPI(title="Helpdesk")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
async def classify_ticket(ticket: TicketInput) -> Classification:
    return await classification.classify(ticket, language_model)


@app.post("/tickets/reply-suggestion")
async def suggest_reply(ticket: TicketInput) -> ReplySuggestion:
    return await suggestion.suggest(ticket, language_model)


@app.post("/tickets/extraction")
async def extract_order_data(ticket: TicketInput) -> OrderData:
    return await extraction.extract(ticket, language_model)


@app.post("/reports/topics")
async def topics_report(period: PeriodInput) -> TopicsReport:
    return await report.topics(period, language_model, ticket_source)
