from openai import AsyncOpenAI

from ..ports import LanguageModel


class GatewayLanguageModel(LanguageModel):
    """Único componente da app que chama o AI Gateway (LiteLLM, API compatível com OpenAI).

    Manda só o nome lógico da capacidade como `model`: modelo físico, provider, retry e
    fallback são decisões do gateway. Por isso não envia `timeout`/`num_retries` no corpo
    nem headers `x-litellm-*`, que sobrescreveriam a política de lá.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float, max_tokens: int):
        # max_retries=0: quem repete é o gateway; o timeout aqui é só rede de segurança.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout)
        self._max_tokens = max_tokens

    async def complete(self, capability: str, task: str, content: str) -> str:
        response = await self._client.chat.completions.create(
            model=capability,
            messages=[{"role": "user", "content": f"TASK: {task}\n{content}"}],
            # Sempre explícito: sem ele o LiteLLM aplica um padrão próprio por provider (ex.: 4096).
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content
