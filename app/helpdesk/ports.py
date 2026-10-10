from datetime import date
from typing import Protocol


class LanguageModel(Protocol):
    """O que as features precisam de um modelo de linguagem.

    `capability` é o nome lógico que o gateway resolve para um modelo físico
    (classify, suggest, topics, extract); `task` e `content` formam o pedido.
    """

    async def complete(self, capability: str, task: str, content: str) -> str: ...


class TicketSource(Protocol):
    """De onde o relatório lê os tickets.

    Devolve os tickets criados entre `start` e `end` (inclusive), na ordem da fonte,
    como dicionários com ao menos `id`, `text` e `created_at`.
    """

    async def in_period(self, start: date, end: date) -> list[dict]: ...
