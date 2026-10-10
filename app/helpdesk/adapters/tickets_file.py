import asyncio
import json
from datetime import date, datetime

from ..ports import TicketSource


class FileTicketSource(TicketSource):
    """Tickets num arquivo JSON Lines (um por linha), relido a cada consulta.

    O caminho vem pelo construtor (quem o conhece é o ponto de composição, não o adapter).
    """

    def __init__(self, path: str):
        self._path = path

    async def in_period(self, start: date, end: date) -> list[dict]:
        # Leitura bloqueante (~10 ms para as 5.000 linhas) fora do event loop.
        return await asyncio.to_thread(self._read, start, end)

    def _read(self, start: date, end: date) -> list[dict]:
        selected = []
        with open(self._path, encoding="utf-8") as file:
            for line in file:
                ticket = json.loads(line)
                day = datetime.fromisoformat(ticket["created_at"]).date()
                if start <= day <= end:
                    selected.append(ticket)
        return selected
