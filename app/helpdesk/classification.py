import json

from fastapi import HTTPException

from .ports import LanguageModel
from .schemas import Classification, TicketInput

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "classify"
CATEGORIES = {"delivery", "payment", "exchange_return", "product_defect", "other"}
PRIORITIES = {"low", "medium", "high"}


async def classify(ticket: TicketInput, model: LanguageModel) -> Classification:
    text = await model.complete(CAPABILITY, "classify", ticket.text)
    data = json.loads(text)
    if data.get("category") not in CATEGORIES or data.get("priority") not in PRIORITIES:
        raise HTTPException(status_code=502, detail="Classificação fora dos valores permitidos")
    return Classification(ticket_id=ticket.ticket_id, **data)
