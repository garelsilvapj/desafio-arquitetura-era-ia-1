from .ports import LanguageModel
from .schemas import ReplySuggestion, TicketInput

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "suggest"


async def suggest(ticket: TicketInput, model: LanguageModel) -> ReplySuggestion:
    text = await model.complete(CAPABILITY, "suggest", ticket.text)
    return ReplySuggestion(ticket_id=ticket.ticket_id, suggestion=text)
