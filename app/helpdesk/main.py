import json
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from . import classification, config, extraction, report, suggestion
from .adapters.gateway import GatewayLanguageModel
from .adapters.tickets_file import FileTicketSource
from .jobs import Job, JobRegistry
from .schemas import Classification, JobStatus, OrderData, PeriodInput, TicketInput, TopicsReport

logger = logging.getLogger(__name__)

# Ponto de composição: único lugar que lê a configuração e conhece os adapters concretos;
# as features recebem as dependências pelos ports (ports.LanguageModel, ports.TicketSource).
language_model = GatewayLanguageModel(
    base_url=config.GATEWAY_BASE_URL,
    api_key=config.GATEWAY_API_KEY,
    timeout=config.GATEWAY_TIMEOUT_SECONDS,
    max_tokens=config.MAX_OUTPUT_TOKENS,
)
ticket_source = FileTicketSource(config.TICKETS_FILE)
topics_jobs = JobRegistry(keep=config.TOPICS_JOBS_KEPT)

# Intervalo sugerido entre consultas ao estado de um relatório.
POLL_SECONDS = "5"
# A borda (nginx) roda com proxy_buffering on; X-Accel-Buffering: no desliga esse buffer
# para esta resposta. Medido com a config atual, os trechos chegam evento a evento com ou
# sem o header; ele fica como defesa barata (ver ADR 0004, "Achado sobre a borda").
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

app = FastAPI(title="Helpdesk")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
async def classify_ticket(ticket: TicketInput) -> Classification:
    return await classification.classify(ticket, language_model)


@app.post("/tickets/reply-suggestion")
async def suggest_reply(ticket: TicketInput) -> StreamingResponse:
    # Só responde 200 depois do 1º trecho; antes disso, falha vira 503 (HTTPException).
    chunks = await suggestion.suggest(ticket, language_model)
    return StreamingResponse(_sse(ticket.ticket_id, chunks), media_type="text/event-stream", headers=SSE_HEADERS)


async def _sse(ticket_id: str, chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        async for chunk in chunks:
            yield _event({"chunk": chunk})
    except Exception as error:
        # O 200 já foi enviado: a falha (LanguageModelUnavailable ou outra) vira evento.
        logger.warning("sugestão de %s interrompida: %r", ticket_id, error)
        yield _event({"ticket_id": ticket_id, "message": "A geração foi interrompida"}, "error")
        return
    yield _event({"ticket_id": ticket_id}, "end")


def _event(data: dict, name: str | None = None) -> str:
    line = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return f"event: {name}\n{line}" if name else line


@app.post("/tickets/extraction")
async def extract_order_data(ticket: TicketInput) -> OrderData:
    return await extraction.extract(ticket, language_model)


# Relatório de tópicos: Asynchronous Request-Reply. O mês leva bem mais que os 30 s da
# borda, então o POST só valida e cria o job; o cliente consulta o estado e busca o resultado.
@app.post("/reports/topics", status_code=202)
async def topics_report(period: PeriodInput) -> JSONResponse:
    selected = await report.select(period, ticket_source)
    job = topics_jobs.start(len(selected), lambda progress: report.topics(
        period, selected, language_model, progress, config.TOPICS_CONCURRENT_BATCHES))
    return JSONResponse(job.status().model_dump(exclude_none=True), status_code=202,
                        headers={"Location": _status_url(job.id), "Retry-After": POLL_SECONDS})


@app.get("/reports/topics/status/{job_id}", response_model=JobStatus, response_model_exclude_none=True)
async def topics_report_status(job_id: str, response: Response):
    job = _job(job_id)
    if job.state == "done":
        return RedirectResponse(f"/reports/topics/{job.id}", status_code=303)
    if job.state != "failed":
        response.headers["Retry-After"] = POLL_SECONDS
    return job.status()


@app.get("/reports/topics/{job_id}")
async def topics_report_result(job_id: str) -> TopicsReport:
    job = _job(job_id)
    if job.state == "failed":
        raise HTTPException(status_code=409, detail=f"O relatório falhou: {job.reason}")
    if job.state != "done":
        raise HTTPException(status_code=409, headers={"Location": _status_url(job.id), "Retry-After": POLL_SECONDS},
                            detail=f"O relatório ainda está sendo gerado ({job.processed}/{job.total} tickets); "
                                   f"consulte {_status_url(job.id)}")
    return job.report


def _job(job_id: str) -> Job:
    job = topics_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado (id desconhecido ou já descartado)")
    return job


def _status_url(job_id: str) -> str:
    # Relativa: a borda repassa o Host sem a porta, então uma URL absoluta sairia errada.
    return f"/reports/topics/status/{job_id}"
