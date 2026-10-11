import asyncio
import json
from collections.abc import Callable

from fastapi import HTTPException

from .ports import LanguageModel, LanguageModelUnavailable, TicketSource
from .schemas import PeriodInput, Topic, TopicsReport

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "topics"
# Tickets por chamada. Não varia por ambiente: é o quanto cabe no contexto do modelo que
# atende `topics` (8.000 tokens hoje); 150 tickets dão ~6,5k tokens (máx. 6.729 em agosto).
TICKETS_PER_CALL = 150
# Prazo de um lote: o pior caso medido no gateway é 45,07 s (15 + 15 + 15 s, os dois
# providers pendurados). Com 50 s, um job que não tem como terminar vira `failed` em menos
# de 60 s, e nunca fica preso em `running`.
BATCH_SECONDS = 50


async def select(period: PeriodInput, tickets: TicketSource) -> list[dict]:
    """Parte síncrona do pedido: valida o período e lê os tickets dele (~10 ms)."""
    if period.start > period.end:
        raise HTTPException(status_code=422, detail="A data inicial é posterior à data final")
    return await tickets.in_period(period.start, period.end)


async def topics(period: PeriodInput, selected: list[dict], model: LanguageModel,
                 progress: Callable[[int], None], concurrency: int) -> TopicsReport:
    """Gera o relatório dos tickets já selecionados.

    O mês inteiro não cabe numa chamada: processa em lotes, até `concurrency` ao mesmo
    tempo, e junta as respostas na ordem ORIGINAL dos lotes, então o relatório é igual ao
    do processamento sequencial. `progress` recebe o número de tickets dos lotes já
    concluídos. Se um lote falhar ou estourar o prazo, os outros são cancelados e sai
    `LanguageModelUnavailable` com o motivo.
    """
    batches = [selected[first:first + TICKETS_PER_CALL] for first in range(0, len(selected), TICKETS_PER_CALL)]
    answers: list[list[dict]] = [[] for _ in batches]
    slots = asyncio.Semaphore(concurrency)
    processed = 0

    async def summarize(index: int) -> None:
        nonlocal processed
        batch = batches[index]
        label = f"Lote {index + 1} de {len(batches)}"
        content = "\n".join(f"[{t['id']}] {t['text']}" for t in batch)
        async with slots:
            try:
                text = await asyncio.wait_for(model.complete(CAPABILITY, "topics", content), BATCH_SECONDS)
            except TimeoutError as error:
                raise LanguageModelUnavailable(f"{label} sem resposta em {BATCH_SECONDS} s") from error
            except LanguageModelUnavailable as error:
                raise LanguageModelUnavailable(f"{label} falhou: {error}") from error
        answers[index] = json.loads(text)["topics"]
        processed += len(batch)
        progress(processed)

    tasks = [asyncio.create_task(summarize(index)) for index in range(len(batches))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # Um lote falhou (ou o job foi cancelado): não adianta pagar pelos outros.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    totals: dict[str, dict] = {}
    for answer in answers:
        for item in answer:
            current = totals.setdefault(item["topic"], {"count": 0, "examples": []})
            current["count"] += item["count"]
            current["examples"] = (current["examples"] + item["examples"])[:3]

    items = [Topic(topic=name, **data) for name, data in totals.items()]
    items.sort(key=lambda t: (-t.count, t.topic))
    return TopicsReport(start=period.start, end=period.end, total_tickets=len(selected), topics=items)
