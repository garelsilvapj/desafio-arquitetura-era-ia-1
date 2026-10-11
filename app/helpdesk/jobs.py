import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from .ports import LanguageModelUnavailable
from .schemas import JobStatus, TopicsReport

logger = logging.getLogger(__name__)

# O trabalho de um job: recebe o callback de progresso (tickets concluídos) e devolve o relatório.
Work = Callable[[Callable[[int], None]], Awaitable[TopicsReport]]


class Job:
    def __init__(self, total: int):
        self.id = uuid.uuid4().hex
        self.state = "pending"
        self.processed = 0
        self.total = total
        self.reason: str | None = None
        self.report: TopicsReport | None = None
        self.task: asyncio.Task | None = None  # referência forte: o event loop só guarda uma fraca

    def advance(self, processed: int) -> None:
        self.processed = processed

    def status(self) -> JobStatus:
        return JobStatus(job_id=self.id, state=self.state, progress=f"{self.processed}/{self.total}",
                         reason=self.reason)


class JobRegistry:
    """Jobs do relatório de tópicos (Asynchronous Request-Reply), na memória do processo.

    Cada job é uma tarefa asyncio no próprio processo da app: sem fila, worker nem banco,
    então um restart da app perde os jobs. Guarda só os `keep` mais recentes; os mais
    antigos já terminados (done/failed) são descartados primeiro.
    """

    def __init__(self, keep: int):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._keep = keep

    def start(self, total: int, work: Work) -> Job:
        job = Job(total)
        self._jobs[job.id] = job
        self._forget_finished()
        job.task = asyncio.create_task(self._run(job, work))
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def _run(self, job: Job, work: Work) -> None:
        job.state = "running"
        # Todo caminho termina em done ou failed: um job nunca fica preso em `running`.
        try:
            job.report = await work(job.advance)
            job.state = "done"
        except LanguageModelUnavailable as error:
            job.reason, job.state = str(error), "failed"
        except Exception:
            logger.exception("job %s falhou", job.id)
            job.reason, job.state = "Falha inesperada ao gerar o relatório (detalhes no log da app)", "failed"

    def _forget_finished(self) -> None:
        finished = [job_id for job_id, job in self._jobs.items() if job.state in ("done", "failed")]
        for job_id in finished[:max(0, len(self._jobs) - self._keep)]:
            del self._jobs[job_id]
