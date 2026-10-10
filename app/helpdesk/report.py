import json

from fastapi import HTTPException

from .ports import LanguageModel, TicketSource
from .schemas import PeriodInput, Topic, TopicsReport

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "topics"
# Tickets por chamada. Não varia por ambiente: é o quanto cabe no contexto do modelo que
# atende `topics` (8.000 tokens hoje); 150 tickets dão ~6,5k tokens (máx. 6.729 em agosto).
TICKETS_PER_CALL = 150


async def topics(period: PeriodInput, model: LanguageModel, tickets: TicketSource) -> TopicsReport:
    if period.start > period.end:
        raise HTTPException(status_code=422, detail="A data inicial é posterior à data final")

    selected = await tickets.in_period(period.start, period.end)

    # O mês inteiro não cabe numa chamada: processa em lotes, um depois do outro.
    totals: dict[str, dict] = {}
    for first in range(0, len(selected), TICKETS_PER_CALL):
        batch = selected[first:first + TICKETS_PER_CALL]
        content = "\n".join(f"[{t['id']}] {t['text']}" for t in batch)
        answer = json.loads(await model.complete(CAPABILITY, "topics", content))
        for item in answer["topics"]:
            current = totals.setdefault(item["topic"], {"count": 0, "examples": []})
            current["count"] += item["count"]
            current["examples"] = (current["examples"] + item["examples"])[:3]

    items = [Topic(topic=name, **data) for name, data in totals.items()]
    items.sort(key=lambda t: (-t.count, t.topic))
    return TopicsReport(start=period.start, end=period.end, total_tickets=len(selected), topics=items)
