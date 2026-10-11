import asyncio
import json

from fastapi import HTTPException

from .ports import LanguageModel, LanguageModelUnavailable
from .schemas import Classification, TicketInput

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "classify"
CATEGORIES = {"delivery", "payment", "exchange_return", "product_defect", "other"}
PRIORITIES = {"low", "medium", "high"}
# Síncrona: o cliente espera a resposta inteira (~1,1 s). Prazo total da app: o pior caso
# medido no gateway é 12,06 s (os dois providers pendurados: 4 + 4 + 4 s); com 13 s sai
# sucesso pelo fallback ou erro explícito antes dos 15 s do critério e dos 30 s da borda.
DEADLINE_SECONDS = 13
UNAVAILABLE = ("Classificação indisponível no momento: nenhum modelo de linguagem conseguiu responder. "
               "Tente de novo em instantes.")


async def classify(ticket: TicketInput, model: LanguageModel) -> Classification:
    try:
        text = await asyncio.wait_for(model.complete(CAPABILITY, "classify", ticket.text), DEADLINE_SECONDS)
    except (LanguageModelUnavailable, TimeoutError) as error:
        raise HTTPException(status_code=503, detail=UNAVAILABLE, headers={"Retry-After": "10"}) from error
    data = json.loads(text)
    if data.get("category") not in CATEGORIES or data.get("priority") not in PRIORITIES:
        raise HTTPException(status_code=502, detail="Classificação fora dos valores permitidos")
    return Classification(ticket_id=ticket.ticket_id, **data)
