import asyncio
from collections.abc import AsyncIterator

from fastapi import HTTPException

from .ports import LanguageModel, LanguageModelUnavailable
from .schemas import TicketInput

# Nome lógico da capacidade no gateway; qual modelo físico a atende é decidido lá.
CAPABILITY = "suggest"
# Streaming: o texto leva ~16 s, mas o 1º trecho chega em ~0,8 s. Prazo até o 1º trecho,
# antes de responder 200: o pior caso medido no gateway é 15,08 s (três stream_timeout de
# 5 s: primário, técnico e fraco); 20 s fica abaixo dos 30 s da borda.
FIRST_CHUNK_SECONDS = 20
# Rede de segurança entre trechos: o gateway já corta em 5 s (stream_timeout); 10 s fica
# abaixo dos 30 s sem bytes em que a borda derruba a conexão.
NEXT_CHUNK_SECONDS = 10
UNAVAILABLE = ("Sugestão de resposta indisponível no momento: nenhum modelo de linguagem conseguiu responder. "
               "Tente de novo em instantes.")


async def suggest(ticket: TicketInput, model: LanguageModel) -> AsyncIterator[str]:
    """Abre a sugestão em streaming e só devolve quando o 1º trecho chegou.

    Falha antes do 1º trecho vira 503 (ainda dá para responder um erro HTTP normal).
    Depois dele, a iteração dos trechos levanta `LanguageModelUnavailable` se a geração
    for interrompida ou parar por mais de NEXT_CHUNK_SECONDS.
    """
    chunks = model.stream(CAPABILITY, "suggest", ticket.text)
    try:
        first = await asyncio.wait_for(anext(chunks), FIRST_CHUNK_SECONDS)
    except (LanguageModelUnavailable, TimeoutError, StopAsyncIteration) as error:
        raise HTTPException(status_code=503, detail=UNAVAILABLE, headers={"Retry-After": "10"}) from error
    return _following(first, chunks)


async def _following(first: str, chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    yield first
    while True:
        try:
            async with asyncio.timeout(NEXT_CHUNK_SECONDS):
                chunk = await anext(chunks)
        except StopAsyncIteration:
            return
        except TimeoutError as error:
            raise LanguageModelUnavailable(f"nenhum trecho novo em {NEXT_CHUNK_SECONDS} s") from error
        yield chunk
