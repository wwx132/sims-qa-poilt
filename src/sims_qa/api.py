from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from .ai import QwenAI
from .doris import DorisClient
from .retrieval import ESRetriever
from .settings import Settings
from .workflow import AskWorkflow


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    allow_exploration: bool = False


class AskResponse(BaseModel):
    type: Literal["answer", "clarify", "error"]
    text: str
    request_id: str
    code: str | None = None
    is_exploratory: bool = False


def build_workflow(settings: Settings) -> AskWorkflow:
    return AskWorkflow(QwenAI(settings), ESRetriever(settings), DorisClient(settings), settings)


def create_app(workflow: Any | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="SIMS project 问数试点", version="0.1.0")
    app.state.workflow = workflow or build_workflow(settings)
    app.state.settings = settings

    @app.get("/health")
    async def health() -> dict[str, Any]:
        missing = settings.missing_services()
        return {"status": "ready" if not missing else "needs_configuration", "missing": missing}

    @app.post("/ask", response_model=AskResponse)
    async def ask(request: AskRequest, response: Response) -> AskResponse:
        request_id = uuid4().hex
        response.headers["X-Request-ID"] = request_id
        try:
            result = await app.state.workflow.ask_result(
                request.question.strip(), session_id=request.session_id, request_id=request_id,
                allow_exploration=request.allow_exploration,
            )
            return AskResponse(
                type=result.type, text=result.text, request_id=result.request_id,
                code=result.code, is_exploratory=result.is_exploratory,
            )
        except Exception as exc:
            # Keep connection strings, SQL and model response bodies out of client errors.
            raise HTTPException(
                status_code=502,
                detail="问数执行失败，请查看服务日志",
                headers={"X-Request-ID": request_id},
            ) from exc

    return app


app = create_app()
