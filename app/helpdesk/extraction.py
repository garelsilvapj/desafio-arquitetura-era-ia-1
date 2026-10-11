import asyncio
import json
import re

from fastapi import HTTPException

from .ports import LanguageModel, LanguageModelUnavailable
from .schemas import OrderData, TicketInput

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "extract"
ORDER_FORMAT = re.compile(r"^#\d{6}$")
# Síncrona, como a classificação: pior caso medido no gateway 10,07 s; prazo total de 13 s.
DEADLINE_SECONDS = 13
# A extração alimenta uma automação sem revisão humana: sem os modelos confiáveis, o certo
# é parar e mandar para o tratamento manual (o gateway não tem modelo fraco para `extract`).
UNAVAILABLE = ("Extração indisponível no momento: os modelos confiáveis para extração falharam e, por política, "
               "nenhum modelo menos confiável é usado. Encaminhe o ticket para tratamento manual.")


async def extract(ticket: TicketInput, model: LanguageModel) -> OrderData:
    try:
        text = await asyncio.wait_for(model.complete(CAPABILITY, "extract", ticket.text), DEADLINE_SECONDS)
    except (LanguageModelUnavailable, TimeoutError) as error:
        raise HTTPException(status_code=503, detail=UNAVAILABLE, headers={"Retry-After": "10"}) from error
    data = json.loads(text)
    order_number = data.get("order_number")
    if order_number is not None and not ORDER_FORMAT.match(order_number):
        raise HTTPException(status_code=502, detail="Número de pedido em formato inválido")
    return OrderData(ticket_id=ticket.ticket_id, order_number=order_number, product=data.get("product"))
