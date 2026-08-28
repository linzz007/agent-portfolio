"""HarnessRuntime wrapper around the existing OrchestrationRunner."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from backend.schemas import ChatMessage, Plan
from core.harness.artifact import ArtifactStore, RunArtifact
from core.harness.hooks import LifecycleHooks, MetricsHook
from core.harness.observability import build_run_diagnostics, build_run_timeline
from core.harness.session import HarnessSession
from core.harness.skills import SkillRegistry, default_skill_registry
from core.metrics import add_event, get_active_trace, trace_scope


class HarnessRuntime:
    """Thin governance layer that keeps the current runner behavior intact."""

    def __init__(
        self,
        runner: Any,
        *,
        artifact_store: Optional[ArtifactStore] = None,
        skill_registry: Optional[SkillRegistry] = None,
        hooks: Optional[LifecycleHooks] = None,
    ):
        self.runner = runner
        self.artifact_store = artifact_store or ArtifactStore()
        self.skill_registry = skill_registry or default_skill_registry()
        self.hooks = hooks or LifecycleHooks([MetricsHook()])
        self.logger = logging.getLogger("harness.runtime")
        self.last_artifact: Optional[RunArtifact] = None
        self.last_artifact_path: Optional[str] = None

    @staticmethod
    def _model_dict(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, dict):
            return value
        return value

    @staticmethod
    def _trace_events(trace: Any) -> List[Dict[str, Any]]:
        if trace is None:
            return []
        events = getattr(trace, "events", None) or []
        return [dict(event) for event in events if isinstance(event, dict)]

    @staticmethod
    def _latest_event(events: List[Dict[str, Any]], event_type: str) -> Optional[Dict[str, Any]]:
        for event in reversed(events):
            if event.get("type") == event_type:
                return dict(event)
        return None

    @staticmethod
    def _tool_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("type", ""))
            if event_type.startswith("tool_") or event_type == "react_phase":
                out.append(dict(event))
        return out

    @staticmethod
    def _user_goal(session: HarnessSession, plan: Optional[Plan]) -> Dict[str, Any]:
        plan_dict = HarnessRuntime._model_dict(plan) if plan is not None else {}
        if not isinstance(plan_dict, dict):
            plan_dict = {}
        return {
            "message": session.user_message,
            "course_name": session.course_name,
            "mode": session.mode,
            "skill_id": session.skill_id,
            "task_type": plan_dict.get("task_type"),
            "need_rag": plan_dict.get("need_rag"),
            "allowed_tools": plan_dict.get("allowed_tools", []),
            "output_format": plan_dict.get("output_format"),
        }

    @staticmethod
    def _memory_trace(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        reads: List[Dict[str, Any]] = []
        writes: List[Dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("type", ""))
            if event_type in {"memory_read", "memory_search"}:
                reads.append(dict(event))
            elif event_type.startswith("memory_write") or event_type in {
                "memory_saved",
                "memory_save_failed",
                "memory_write_decision",
            }:
                writes.append(dict(event))
        return {
            "reads": reads,
            "writes": writes,
            "read_count": len(reads),
            "write_count": len(writes),
        }

    @staticmethod
    def _tool_decisions(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        decisions: List[Dict[str, Any]] = []
        for event in events:
            if event.get("type") != "tool_gate_decision":
                continue
            allowed = bool(event.get("tool_gate_decision", False))
            risk_level = str(event.get("risk_level") or "safe")
            decisions.append(
                {
                    "tool_name": event.get("tool_name"),
                    "phase": event.get("phase"),
                    "allowed": allowed,
                    "reason": "allowed" if allowed else event.get("tool_skip_reason", "blocked"),
                    "risk_level": risk_level,
                    "approval_mode": str(event.get("approval_mode") or "off"),
                    "tool_signature": event.get("tool_signature"),
                    "tool_round": event.get("tool_round"),
                    "source_event_seq": event.get("seq"),
                }
            )
        return decisions

    @staticmethod
    def _risk_decisions(tool_decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        riskful = {"external", "write"}
        out: List[Dict[str, Any]] = []
        for decision in tool_decisions:
            risk_level = str(decision.get("risk_level") or "safe")
            if risk_level in riskful or not bool(decision.get("allowed", False)):
                out.append(dict(decision))
        return out

    @staticmethod
    def _eval_result(
        *,
        output: Optional[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
        context_budget: Optional[Dict[str, Any]],
        tool_decisions: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]],
        error: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from core.harness.evaluation import evaluate_artifact

        return evaluate_artifact(
            {
                "output": output,
                "retrieval": retrieval,
                "context_budget": context_budget,
                "tool_calls": tool_calls,
                "tool_decisions": tool_decisions,
                "error": error,
            }
        )

    @staticmethod
    def _citations(response: Optional[ChatMessage]) -> List[Dict[str, Any]]:
        if response is None or not response.citations:
            return []
        return [HarnessRuntime._model_dict(item) for item in response.citations]

    def _emit(
        self,
        name: str,
        session: HarnessSession,
        lifecycle_events: List[Dict[str, Any]],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = self.hooks.emit(name, session, payload)
        lifecycle_events.append(event.to_dict())

    def _persist(self, artifact: RunArtifact) -> None:
        self.last_artifact = artifact
        try:
            self.last_artifact_path = self.artifact_store.write(artifact)
            add_event(
                "harness_artifact_written",
                run_id=artifact.run_id,
                artifact_path=self.last_artifact_path,
            )
        except Exception as exc:
            self.last_artifact_path = None
            self.logger.warning(
                "[harness] artifact_write_failed run_id=%s err=%s",
                artifact.run_id,
                str(exc),
            )
            add_event(
                "harness_artifact_write_failed",
                run_id=artifact.run_id,
                error=str(exc),
            )

    def _build_artifact(
        self,
        *,
        session: HarnessSession,
        trace: Any,
        elapsed_ms: float,
        lifecycle_events: List[Dict[str, Any]],
        response: Optional[ChatMessage] = None,
        plan: Optional[Plan] = None,
        error: Optional[BaseException] = None,
    ) -> RunArtifact:
        events = self._trace_events(trace)
        response_tool_calls = []
        if response is not None and response.tool_calls:
            response_tool_calls = [
                {"source": "response", **dict(item)}
                for item in response.tool_calls
                if isinstance(item, dict)
            ]
        trace_tool_calls = [{"source": "trace", **event} for event in self._tool_events(events)]
        metrics = {
            "trace_id": getattr(trace, "trace_id", None),
            "event_count": len(events),
            "elapsed_ms": elapsed_ms,
            "trace_events": events,
        }
        err_payload = None
        if error is not None:
            err_payload = {
                "type": error.__class__.__name__,
                "message": str(error),
            }
        retrieval = self._citations(response)
        context_budget = self._latest_event(events, "context_budget")
        tool_decisions = self._tool_decisions(events)
        tool_calls = response_tool_calls + trace_tool_calls
        output = self._model_dict(response)
        eval_result = self._eval_result(
            output=output,
            retrieval=retrieval,
            context_budget=context_budget,
            tool_calls=tool_calls,
            tool_decisions=tool_decisions,
            error=err_payload,
        )
        session_payload = session.to_dict()
        return RunArtifact(
            run_id=session.run_id,
            session=session_payload,
            plan=self._model_dict(plan),
            user_goal=self._user_goal(session, plan),
            retrieval=retrieval,
            context_budget=context_budget,
            memory_trace=self._memory_trace(events),
            tool_calls=tool_calls,
            tool_decisions=tool_decisions,
            risk_decisions=self._risk_decisions(tool_decisions),
            timeline=build_run_timeline(
                session=session_payload,
                events=events,
                retrieval=retrieval,
                context_budget=context_budget,
                tool_calls=tool_calls,
                tool_decisions=tool_decisions,
                output=output,
                eval_result=eval_result,
                error=err_payload,
                elapsed_ms=elapsed_ms,
            ),
            diagnostics=build_run_diagnostics(
                session=session_payload,
                retrieval=retrieval,
                context_budget=context_budget,
                tool_calls=tool_calls,
                tool_decisions=tool_decisions,
                output=output,
                error=err_payload,
            ),
            output=output,
            eval_result=eval_result,
            metrics=metrics,
            error=err_payload,
            lifecycle_events=lifecycle_events,
        )

    def _build_stream_artifact(
        self,
        *,
        session: HarnessSession,
        trace: Any,
        elapsed_ms: float,
        lifecycle_events: List[Dict[str, Any]],
        output_text: str,
        chunk_count: int,
        stream_events: List[Dict[str, Any]],
        citations: List[Dict[str, Any]],
        context_budget: Optional[Dict[str, Any]] = None,
        response_tool_calls: Optional[List[Dict[str, Any]]] = None,
        error: Optional[BaseException] = None,
    ) -> RunArtifact:
        events = self._trace_events(trace)
        trace_tool_calls = [{"source": "trace", **event} for event in self._tool_events(events)]
        stream_tool_calls = [
            {"source": "stream", **dict(item)}
            for item in (response_tool_calls or [])
            if isinstance(item, dict)
        ]
        metrics = {
            "trace_id": getattr(trace, "trace_id", None),
            "event_count": len(events),
            "elapsed_ms": elapsed_ms,
            "stream_chunk_count": chunk_count,
            "stream_event_count": len(stream_events),
            "trace_events": events,
        }
        err_payload = None
        if error is not None:
            err_payload = {
                "type": error.__class__.__name__,
                "message": str(error),
            }
        context_payload = context_budget or self._latest_event(events, "context_budget")
        tool_decisions = self._tool_decisions(events)
        tool_calls = stream_tool_calls + trace_tool_calls
        output = {
            "role": "assistant",
            "content": output_text,
            "stream": True,
            "chunk_count": chunk_count,
            "events": stream_events,
        }
        eval_result = self._eval_result(
            output=output,
            retrieval=citations,
            context_budget=context_payload,
            tool_calls=tool_calls,
            tool_decisions=tool_decisions,
            error=err_payload,
        )
        session_payload = session.to_dict()
        return RunArtifact(
            run_id=session.run_id,
            session=session_payload,
            plan=None,
            user_goal=self._user_goal(session, None),
            retrieval=citations,
            context_budget=context_payload,
            memory_trace=self._memory_trace(events),
            tool_calls=tool_calls,
            tool_decisions=tool_decisions,
            risk_decisions=self._risk_decisions(tool_decisions),
            timeline=build_run_timeline(
                session=session_payload,
                events=events,
                retrieval=citations,
                context_budget=context_payload,
                tool_calls=tool_calls,
                tool_decisions=tool_decisions,
                output=output,
                eval_result=eval_result,
                error=err_payload,
                elapsed_ms=elapsed_ms,
            ),
            diagnostics=build_run_diagnostics(
                session=session_payload,
                retrieval=citations,
                context_budget=context_payload,
                tool_calls=tool_calls,
                tool_decisions=tool_decisions,
                output=output,
                error=err_payload,
            ),
            output=output,
            eval_result=eval_result,
            metrics=metrics,
            error=err_payload,
            lifecycle_events=lifecycle_events,
        )

    def run(
        self,
        *,
        course_name: str,
        mode: str,
        user_message: str,
        state: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> Tuple[ChatMessage, Plan]:
        """Run the existing orchestration flow and persist a RunArtifact."""

        history = list(history or [])
        skill = self.skill_registry.resolve(mode=mode, user_message=user_message, history=history)
        active_trace = get_active_trace()
        trace_meta = {
            "request_id": request_id,
            "course_name": course_name,
            "mode": mode,
            "skill_id": skill.skill_id,
            "api": "harness",
        }
        cm = nullcontext(active_trace) if active_trace is not None else trace_scope(trace_meta)
        with cm as trace:
            if trace is not None and isinstance(getattr(trace, "meta", None), dict):
                trace.meta.update({"skill_id": skill.skill_id})
            session = HarnessSession.create(
                course_name=course_name,
                mode=mode,
                skill_id=skill.skill_id,
                user_message=user_message,
                trace_id=getattr(trace, "trace_id", None),
                request_id=request_id,
            )
            if trace is not None and isinstance(getattr(trace, "meta", None), dict):
                trace.meta.update({"run_id": session.run_id})

            lifecycle_events: List[Dict[str, Any]] = []
            t0 = perf_counter()
            response: Optional[ChatMessage] = None
            plan: Optional[Plan] = None
            try:
                self._emit(
                    "before_plan",
                    session,
                    lifecycle_events,
                    {
                        "mode": mode,
                        "skill_id": skill.skill_id,
                        "message_chars": len(user_message or ""),
                    },
                )
                response, plan = self.runner.run(
                    course_name=course_name,
                    mode=mode,
                    user_message=user_message,
                    state=state or {},
                    history=history,
                )
                self._emit(
                    "after_plan",
                    session,
                    lifecycle_events,
                    {"plan": self._model_dict(plan)},
                )
                self._emit(
                    "after_answer",
                    session,
                    lifecycle_events,
                    {
                        "output_chars": len(response.content or ""),
                        "has_citations": bool(response.citations),
                        "has_tool_calls": bool(response.tool_calls),
                    },
                )
                session.complete()
                elapsed_ms = (perf_counter() - t0) * 1000.0
                artifact = self._build_artifact(
                    session=session,
                    trace=trace,
                    elapsed_ms=elapsed_ms,
                    lifecycle_events=lifecycle_events,
                    response=response,
                    plan=plan,
                )
                self._persist(artifact)
                return response, plan
            except Exception as exc:
                session.fail()
                self._emit(
                    "on_error",
                    session,
                    lifecycle_events,
                    {"error_type": exc.__class__.__name__, "error": str(exc)},
                )
                elapsed_ms = (perf_counter() - t0) * 1000.0
                artifact = self._build_artifact(
                    session=session,
                    trace=trace,
                    elapsed_ms=elapsed_ms,
                    lifecycle_events=lifecycle_events,
                    response=response,
                    plan=plan,
                    error=exc,
                )
                self._persist(artifact)
                raise

    def run_stream(
        self,
        *,
        course_name: str,
        mode: str,
        user_message: str,
        state: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ):
        """Stream through the existing runner and persist a stream RunArtifact."""

        history = list(history or [])
        skill = self.skill_registry.resolve(mode=mode, user_message=user_message, history=history)
        active_trace = get_active_trace()
        trace_meta = {
            "request_id": request_id,
            "course_name": course_name,
            "mode": mode,
            "skill_id": skill.skill_id,
            "api": "harness.stream",
        }
        cm = nullcontext(active_trace) if active_trace is not None else trace_scope(trace_meta)
        with cm as trace:
            if trace is not None and isinstance(getattr(trace, "meta", None), dict):
                trace.meta.update({"skill_id": skill.skill_id})
            session = HarnessSession.create(
                course_name=course_name,
                mode=mode,
                skill_id=skill.skill_id,
                user_message=user_message,
                trace_id=getattr(trace, "trace_id", None),
                request_id=request_id,
            )
            if trace is not None and isinstance(getattr(trace, "meta", None), dict):
                trace.meta.update({"run_id": session.run_id})

            lifecycle_events: List[Dict[str, Any]] = []
            output_parts: List[str] = []
            stream_events: List[Dict[str, Any]] = []
            citations: List[Dict[str, Any]] = []
            context_budget_payload: Optional[Dict[str, Any]] = None
            response_tool_calls: List[Dict[str, Any]] = []
            chunk_count = 0
            t0 = perf_counter()
            try:
                self._emit(
                    "before_plan",
                    session,
                    lifecycle_events,
                    {
                        "mode": mode,
                        "skill_id": skill.skill_id,
                        "message_chars": len(user_message or ""),
                        "stream": True,
                    },
                )
                for chunk in self.runner.run_stream(
                    course_name=course_name,
                    mode=mode,
                    user_message=user_message,
                    state=state or {},
                    history=history,
                ):
                    chunk_count += 1
                    if isinstance(chunk, str):
                        output_parts.append(chunk)
                    elif isinstance(chunk, dict):
                        stream_events.append(dict(chunk))
                        if "__citations__" in chunk and isinstance(chunk.get("__citations__"), list):
                            citations = [
                                self._model_dict(item)
                                for item in chunk.get("__citations__", [])
                                if isinstance(self._model_dict(item), dict)
                            ]
                        if "__context_budget__" in chunk and isinstance(chunk.get("__context_budget__"), dict):
                            context_budget_payload = dict(chunk.get("__context_budget__") or {})
                        if "__tool_calls__" in chunk and isinstance(chunk.get("__tool_calls__"), list):
                            response_tool_calls.extend(
                                [
                                    dict(item)
                                    for item in chunk.get("__tool_calls__", [])
                                    if isinstance(item, dict)
                                ]
                            )
                    yield chunk

                output_text = "".join(output_parts)
                self._emit(
                    "after_plan",
                    session,
                    lifecycle_events,
                    {
                        "plan": None,
                        "source": "runner.run_stream",
                        "stream": True,
                    },
                )
                self._emit(
                    "after_answer",
                    session,
                    lifecycle_events,
                    {
                        "output_chars": len(output_text),
                        "chunk_count": chunk_count,
                        "has_citations": bool(citations),
                        "has_tool_calls": bool(response_tool_calls),
                        "stream": True,
                    },
                )
                session.complete()
                elapsed_ms = (perf_counter() - t0) * 1000.0
                artifact = self._build_stream_artifact(
                    session=session,
                    trace=trace,
                    elapsed_ms=elapsed_ms,
                    lifecycle_events=lifecycle_events,
                    output_text=output_text,
                    chunk_count=chunk_count,
                    stream_events=stream_events,
                    citations=citations,
                    context_budget=context_budget_payload,
                    response_tool_calls=response_tool_calls,
                )
                self._persist(artifact)
            except Exception as exc:
                session.fail()
                self._emit(
                    "on_error",
                    session,
                    lifecycle_events,
                    {
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                        "stream": True,
                    },
                )
                elapsed_ms = (perf_counter() - t0) * 1000.0
                artifact = self._build_stream_artifact(
                    session=session,
                    trace=trace,
                    elapsed_ms=elapsed_ms,
                    lifecycle_events=lifecycle_events,
                    output_text="".join(output_parts),
                    chunk_count=chunk_count,
                    stream_events=stream_events,
                    citations=citations,
                    context_budget=context_budget_payload,
                    response_tool_calls=response_tool_calls,
                    error=exc,
                )
                self._persist(artifact)
                raise
