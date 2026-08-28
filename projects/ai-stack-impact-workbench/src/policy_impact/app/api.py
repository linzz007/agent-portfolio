"""FastAPI entrypoint for the final policy impact app."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ModuleNotFoundError:  # pragma: no cover - exercised when web extras are not installed
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment,misc]
    FileResponse = None  # type: ignore[assignment]
    StreamingResponse = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]

    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

from policy_impact.app.service import PolicyImpactService
from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.company_wiki.loader import company_dir
from policy_impact.harness.model_gateway import ModelInvocationError


class _FallbackApp:
    title = "智能体 Harness 工作台"

    def get(self, *_args: Any, **_kwargs: Any):
        def decorator(fn):
            return fn

        return decorator

    def post(self, *_args: Any, **_kwargs: Any):
        def decorator(fn):
            return fn

        return decorator

    def mount(self, *_args: Any, **_kwargs: Any) -> None:
        return None


app = (
    FastAPI(title="智能体 Harness 工作台", version="1.0.0")
    if FastAPI is not None
    else _FallbackApp()
)
service = PolicyImpactService()
workbench_service = ChatWorkbenchService()
STATIC_DIR = Path(__file__).with_name("static")
WORKBENCH_INDEX = STATIC_DIR / "workbench" / "index.html"

if FastAPI is not None and StaticFiles is not None:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    question: str
    report_id: str | None = None


class MainChatRequest(BaseModel):
    message: str
    session_id: str = "main"


class WorkbenchSessionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    title: str = "New Chat"
    mode: str = "auto"
    model_id: str = ""
    active_skill_id: str = ""


class WorkbenchSessionUpdateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    title: str | None = None
    mode: str | None = None
    model_id: str | None = None
    active_skill_id: str | None = None


class WorkbenchMessageRequest(BaseModel):
    message: str


class WorkbenchModelConfigRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_id: str
    display_name: str = ""
    provider: str = "openai-compatible"
    model_name: str
    endpoint: str = ""
    api_key: str = ""
    temperature: float = 0.0
    max_tokens: int = 2048
    is_default: bool = False


class WorkbenchWikiPageUpdateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    path: str
    body: str


def _sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _chunk_text(text: str, size: int = 36):
    value = str(text or "")
    for index in range(0, len(value), size):
        yield value[index : index + size]


def _public_stream_result(result: dict[str, Any]) -> dict[str, Any]:
    public = dict(result)
    answer = str(public.pop("answer", "") or "")
    public["answer_length"] = len(answer)
    public["streaming_transport"] = "sse"
    return public


@app.get("/")
def workbench_index():
    if FileResponse is None:
        return {"message": "智能体工作台需要 FastAPI 静态文件支持。"}
    return FileResponse(WORKBENCH_INDEX)


@app.get("/workbench")
def workbench_page():
    if FileResponse is None:
        return {"message": "智能体工作台需要 FastAPI 静态文件支持。"}
    return FileResponse(WORKBENCH_INDEX)


@app.get("/companies")
def list_companies() -> dict[str, Any]:
    summaries = service.list_company_summaries()
    return {"companies": summaries, "company_ids": [item["company_id"] for item in summaries]}


@app.get("/companies/{company_id}")
def company_overview(company_id: str) -> dict[str, Any]:
    try:
        return service.company_overview(company_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/companies/{company_id}/weekly-analysis")
def run_weekly_analysis(company_id: str) -> dict[str, Any]:
    try:
        return service.run_weekly_analysis(company_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=repr(exc)) from exc


@app.post("/companies/{company_id}/recent-news-report")
def run_recent_news_report(company_id: str) -> dict[str, Any]:
    try:
        return service.run_news_analysis(company_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=repr(exc)) from exc


@app.get("/companies/{company_id}/latest-report")
def latest_report(company_id: str) -> dict[str, Any]:
    report = service.latest_report(company_id)
    if not report:
        raise HTTPException(status_code=404, detail="no report found")
    return report


@app.post("/companies/{company_id}/report-chat")
def report_chat(company_id: str, payload: ChatRequest) -> dict[str, Any]:
    try:
        return service.ask_report(company_id, payload.question, report_id=payload.report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/companies/{company_id}/chat")
def main_chat(company_id: str, payload: MainChatRequest) -> dict[str, Any]:
    return service.chat(company_id, payload.message, session_id=payload.session_id)


@app.get("/companies/{company_id}/workbench/models")
def workbench_models(company_id: str) -> dict[str, Any]:
    return {"models": workbench_service.list_models(company_id)}


@app.post("/companies/{company_id}/workbench/models")
def save_workbench_model(company_id: str, payload: WorkbenchModelConfigRequest) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="模型管理已禁用。当前 Workbench 固定使用 deepseek-v4-flash，请通过启动环境变量配置模型。",
    )


@app.get("/companies/{company_id}/workbench/skills")
def workbench_skills(company_id: str) -> dict[str, Any]:
    return {"skills": workbench_service.list_skills()}


@app.get("/companies/{company_id}/workbench/wiki")
def workbench_wiki(company_id: str) -> dict[str, Any]:
    try:
        return workbench_service.wiki_tree(company_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/companies/{company_id}/workbench/wiki/pages")
def update_workbench_wiki_page(
    company_id: str,
    payload: WorkbenchWikiPageUpdateRequest,
) -> dict[str, Any]:
    try:
        return workbench_service.update_wiki_page(company_id, payload.path, payload.body)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/companies/{company_id}/workbench/sessions")
def workbench_sessions(company_id: str) -> dict[str, Any]:
    return {"sessions": workbench_service.list_sessions(company_id)}


@app.post("/companies/{company_id}/workbench/sessions")
def create_workbench_session(
    company_id: str,
    payload: WorkbenchSessionRequest,
) -> dict[str, Any]:
    try:
        return workbench_service.create_session(
            company_id=company_id,
            title=payload.title,
            mode=payload.mode,
            model_id=payload.model_id,
            active_skill_id=payload.active_skill_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/companies/{company_id}/workbench/sessions/{session_id}/settings")
def update_workbench_session(
    company_id: str,
    session_id: str,
    payload: WorkbenchSessionUpdateRequest,
) -> dict[str, Any]:
    try:
        return workbench_service.update_session(
            company_id=company_id,
            session_id=session_id,
            title=payload.title,
            mode=payload.mode,
            model_id=payload.model_id,
            active_skill_id=payload.active_skill_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/companies/{company_id}/workbench/sessions/{session_id}/messages")
def workbench_messages(company_id: str, session_id: str) -> dict[str, Any]:
    return {"messages": workbench_service.list_messages(company_id, session_id)}


@app.get("/companies/{company_id}/workbench/artifact")
def workbench_artifact(company_id: str, path: str):
    if FileResponse is None:
        return {"message": "当前环境不支持文件响应。"}
    company_root = company_dir(company_id).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = company_root / target
    try:
        resolved = target.resolve()
        resolved.relative_to(company_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="artifact path is outside company workspace") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(resolved)


@app.post("/companies/{company_id}/workbench/sessions/{session_id}/messages")
def send_workbench_message(
    company_id: str,
    session_id: str,
    payload: WorkbenchMessageRequest,
) -> dict[str, Any]:
    try:
        return workbench_service.send_message(company_id, session_id, payload.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelInvocationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/companies/{company_id}/workbench/sessions/{session_id}/messages/stream")
def stream_workbench_message(
    company_id: str,
    session_id: str,
    payload: WorkbenchMessageRequest,
):
    if StreamingResponse is None:
        raise HTTPException(status_code=500, detail="StreamingResponse is unavailable")

    def event_stream():
        yield _sse_event(
            "start",
            {
                "session_id": session_id,
                "transport": "sse",
                "message_received": True,
            },
        )
        try:
            result = workbench_service.send_message(company_id, session_id, payload.message)
            yield _sse_event("result", _public_stream_result(result))
            for chunk in _chunk_text(str(result.get("answer") or "")):
                yield _sse_event("chunk", {"content": chunk})
                time.sleep(0.012)
            trace = workbench_service.trace_for_run(
                company_id,
                session_id,
                str(result.get("run_id") or ""),
            )
            yield _sse_event("trace", trace)
            yield _sse_event(
                "done",
                {
                    "session_id": session_id,
                    "run_id": result.get("run_id", ""),
                    "selected_skill_id": result.get("selected_skill_id", ""),
                },
            )
        except KeyError as exc:
            yield _sse_event("error", {"detail": str(exc), "error_type": "not_found"})
        except ModelInvocationError as exc:
            yield _sse_event("error", {"detail": str(exc), "error_type": "model_invocation"})
        except ValueError as exc:
            yield _sse_event("error", {"detail": str(exc), "error_type": "bad_request"})
        except Exception as exc:  # noqa: BLE001
            yield _sse_event(
                "error",
                {
                    "detail": repr(exc),
                    "error_type": type(exc).__name__,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/companies/{company_id}/workbench/sessions/{session_id}/trace")
def workbench_trace(company_id: str, session_id: str) -> dict[str, Any]:
    return workbench_service.latest_trace(company_id, session_id)


@app.get("/companies/{company_id}/workbench/sessions/{session_id}/runs/{run_id}/trace")
def workbench_run_trace(company_id: str, session_id: str, run_id: str) -> dict[str, Any]:
    try:
        return workbench_service.trace_for_run(company_id, session_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/companies/{company_id}/wiki-blueprint")
def wiki_blueprint(company_id: str) -> dict[str, Any]:
    return service.wiki_blueprint(company_id)


@app.get("/companies/{company_id}/corrections")
def pending_corrections(company_id: str) -> dict[str, Any]:
    return {"corrections": service.pending_corrections(company_id)}


@app.post("/companies/{company_id}/corrections/{correction_id}/apply")
def apply_correction(company_id: str, correction_id: str) -> dict[str, Any]:
    try:
        return service.apply_correction(company_id, correction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
