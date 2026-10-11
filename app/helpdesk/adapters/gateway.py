import logging
from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI

from ..ports import LanguageModel, LanguageModelUnavailable

logger = logging.getLogger(__name__)


class GatewayLanguageModel(LanguageModel):
    """Único componente da app que chama o AI Gateway (LiteLLM, API compatível com OpenAI).

    Manda só o nome lógico da capacidade como `model`: modelo físico, provider, retry e
    fallback são decisões do gateway. Por isso não envia `timeout`/`num_retries` no corpo
    nem headers `x-litellm-*`, que sobrescreveriam a política de lá. Toda falha do gateway
    sai daqui como `LanguageModelUnavailable`.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float, max_tokens: int):
        # max_retries=0: quem repete é o gateway. O timeout aqui é só rede de segurança do
        # socket; os prazos de verdade são das features (asyncio), todos abaixo dele.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout)
        # Sempre explícito: sem ele o LiteLLM aplica um padrão próprio por provider (ex.: 4096).
        self._max_tokens = max_tokens

    async def complete(self, capability: str, task: str, content: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=capability, messages=_messages(task, content), max_tokens=self._max_tokens)
        except openai.APIError as error:
            raise _unavailable(capability, error) from error
        return response.choices[0].message.content

    async def stream(self, capability: str, task: str, content: str) -> AsyncIterator[str]:
        try:
            chunks = await self._client.chat.completions.create(
                model=capability, messages=_messages(task, content), max_tokens=self._max_tokens, stream=True)
            # `async with` fecha a conexão com o gateway também quando quem consome desiste.
            async with chunks:
                async for chunk in chunks:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
        # No meio do stream, o erro do gateway (`data: {"error": ...}`) também chega como
        # openai.APIError, e o SDK embrulha falha de rede/timeout da leitura em
        # APIConnectionError/APITimeoutError (subclasses de APIError).
        except openai.APIError as error:
            raise _unavailable(capability, error) from error


def _messages(task: str, content: str) -> list[dict]:
    return [{"role": "user", "content": f"TASK: {task}\n{content}"}]


def _unavailable(capability: str, error: Exception) -> LanguageModelUnavailable:
    # O corpo de erro do gateway cita grupos internos e a cadeia de fallback: fica só no log.
    logger.warning("gateway falhou em %s: %r", capability, error)
    status = getattr(error, "status_code", None)
    cause = f"HTTP {status}" if status else type(error).__name__
    return LanguageModelUnavailable(f"capacidade {capability} indisponível no gateway ({cause})")
