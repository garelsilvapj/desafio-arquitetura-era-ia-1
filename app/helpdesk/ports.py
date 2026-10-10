from typing import Protocol


class LanguageModel(Protocol):
    """O que as features precisam de um modelo de linguagem.

    `capability` é o nome lógico que o gateway resolve para um modelo físico
    (classify, suggest, topics, extract); `task` e `content` formam o pedido.
    """

    async def complete(self, capability: str, task: str, content: str) -> str: ...
