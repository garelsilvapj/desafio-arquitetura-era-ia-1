from collections.abc import AsyncIterator
from datetime import date
from typing import Protocol


class LanguageModelUnavailable(Exception):
    """O modelo de linguagem não atendeu o pedido.

    Única falha que o port promete, para qualquer causa: gateway fora do ar, todos os
    modelos da capacidade falharam, prazo estourado ou geração interrompida no meio do
    stream. A mensagem é legível e não traz detalhe interno do gateway. Quem chama decide
    o que ela vira (503, evento de erro no stream, job `failed`).
    """


class LanguageModel(Protocol):
    """O que as features precisam de um modelo de linguagem.

    `capability` é o nome lógico que o gateway resolve para um modelo físico
    (classify, suggest, topics, extract); `task` e `content` formam o pedido.
    Toda falha chega como `LanguageModelUnavailable`.
    """

    async def complete(self, capability: str, task: str, content: str) -> str: ...

    # Os trechos do texto na ordem em que o modelo os gera. Implementado como gerador
    # assíncrono (`async def` com `yield`), por isso declarado com `def` aqui.
    def stream(self, capability: str, task: str, content: str) -> AsyncIterator[str]: ...


class TicketSource(Protocol):
    """De onde o relatório lê os tickets.

    Devolve os tickets criados entre `start` e `end` (inclusive), na ordem da fonte,
    como dicionários com ao menos `id`, `text` e `created_at`.
    """

    async def in_period(self, start: date, end: date) -> list[dict]: ...
