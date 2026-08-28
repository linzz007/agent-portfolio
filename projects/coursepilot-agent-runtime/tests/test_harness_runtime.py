import tempfile
import unittest

from backend.schemas import ChatMessage, Plan, RetrievedChunk
from core.harness.artifact import ArtifactStore
from core.harness.runtime import HarnessRuntime
from core.metrics import add_event, trace_scope


class _FakeRunner:
    def run(self, course_name, mode, user_message, state=None, history=None):
        add_event("retrieval", mode="hybrid", top_k=4, returned_count=1, success=True)
        add_event("context_budget", final_tokens_est=80, budget_tokens_est=8192)
        add_event("memory_read", query="matrix mistakes", hit_count=2, success=True)
        add_event(
            "memory_write_decision",
            policy="on_grade_only",
            allowed=False,
            reason="learn_mode_no_write",
        )
        add_event(
            "tool_gate_decision",
            tool_name="calculator",
            tool_gate_decision=True,
            tool_round=1,
            risk_level="safe",
            approval_mode="off",
        )
        add_event(
            "tool_gate_decision",
            tool_name="filewriter",
            tool_gate_decision=False,
            tool_skip_reason="approval_required",
            tool_round=1,
            risk_level="write",
            approval_mode="strict",
        )
        return (
            ChatMessage(
                role="assistant",
                content=f"answer: {user_message}",
                citations=[
                    RetrievedChunk(
                        text="source text",
                        doc_id="doc.md",
                        page=1,
                        chunk_id="c1",
                        score=0.9,
                    )
                ],
                tool_calls=[{"name": "quiz_meta", "payload": {"id": 1}}],
            ),
            Plan(
                need_rag=True,
                allowed_tools=["calculator"],
                task_type="learn",
                style="step_by_step",
                output_format="answer",
            ),
        )

    def run_stream(self, course_name, mode, user_message, state=None, history=None):
        add_event("retrieval", mode="hybrid", top_k=4, returned_count=1, success=True)
        add_event("context_budget", final_tokens_est=60, budget_tokens_est=8192)
        yield {"__context_budget__": {"final_tokens_est": 60, "budget_tokens_est": 8192}}
        yield {"__citations__": [{"text": "source text", "doc_id": "doc.md", "page": 1, "chunk_id": "c1", "score": 0.9}]}
        yield "hello "
        yield "stream"
        yield {"__tool_calls__": [{"name": "quiz_meta", "payload": {"id": 1}}]}


class HarnessRuntimeTests(unittest.TestCase):
    def test_runtime_preserves_response_and_writes_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = HarnessRuntime(_FakeRunner(), artifact_store=ArtifactStore(tmp))

            with trace_scope({"request_id": "req-1", "mode": "learn"}) as trace:
                response, plan = runtime.run(
                    course_name="course",
                    mode="learn",
                    user_message="hello",
                    history=[],
                    request_id="req-1",
                )

            self.assertEqual("answer: hello", response.content)
            self.assertTrue(plan.need_rag)
            self.assertIsNotNone(runtime.last_artifact_path)
            data = ArtifactStore.read(runtime.last_artifact_path)

            self.assertEqual("succeeded", data["session"]["status"])
            self.assertEqual("learn.answer.v1", data["session"]["skill_id"])
            self.assertEqual(trace.trace_id, data["session"]["trace_id"])
            self.assertEqual(1, len(data["retrieval"]))
            self.assertEqual(80, data["context_budget"]["final_tokens_est"])
            self.assertTrue(any(x.get("source") == "trace" for x in data["tool_calls"]))
            self.assertEqual("answer: hello", data["output"]["content"])
            self.assertEqual("hello", data["user_goal"]["message"])
            self.assertEqual("learn", data["user_goal"]["mode"])
            self.assertEqual(2, data["memory_trace"]["reads"][0]["hit_count"])
            self.assertFalse(data["memory_trace"]["writes"][0]["allowed"])
            decisions = data["tool_decisions"]
            self.assertTrue(any(x["tool_name"] == "calculator" and x["allowed"] for x in decisions))
            self.assertTrue(any(x["tool_name"] == "filewriter" and not x["allowed"] for x in decisions))
            self.assertTrue(any(x["risk_level"] == "write" for x in data["risk_decisions"]))
            self.assertEqual("heuristic.v1", data["eval_result"]["evaluator"])
            self.assertTrue(data["eval_result"]["checks"]["has_output"])
            self.assertTrue(any(x["type"] == "retrieval" for x in data["timeline"]))
            self.assertTrue(any(x["type"] == "tool_decision" for x in data["timeline"]))
            diagnostics = {x["check"]: x for x in data["diagnostics"]}
            self.assertEqual("ok", diagnostics["run_status"]["status"])
            self.assertEqual("ok", diagnostics["answer_output"]["status"])

    def test_runtime_writes_error_artifact_and_reraises(self):
        class BrokenRunner:
            def run(self, **_kwargs):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            runtime = HarnessRuntime(BrokenRunner(), artifact_store=ArtifactStore(tmp))
            with self.assertRaises(RuntimeError):
                runtime.run(
                    course_name="course",
                    mode="learn",
                    user_message="hello",
                    history=[],
                    request_id="req-2",
                )

            data = ArtifactStore.read(runtime.last_artifact_path)
            self.assertEqual("failed", data["session"]["status"])
            self.assertEqual("RuntimeError", data["error"]["type"])
            self.assertIn("boom", data["error"]["message"])

    def test_stream_runtime_preserves_chunks_and_writes_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = HarnessRuntime(_FakeRunner(), artifact_store=ArtifactStore(tmp))

            with trace_scope({"request_id": "req-stream", "mode": "learn"}) as trace:
                chunks = list(
                    runtime.run_stream(
                        course_name="course",
                        mode="learn",
                        user_message="stream please",
                        history=[],
                        request_id="req-stream",
                    )
                )

            self.assertEqual("hello stream", "".join(x for x in chunks if isinstance(x, str)))
            data = ArtifactStore.read(runtime.last_artifact_path)

            self.assertEqual("succeeded", data["session"]["status"])
            self.assertEqual("learn.answer.v1", data["session"]["skill_id"])
            self.assertEqual(trace.trace_id, data["session"]["trace_id"])
            self.assertTrue(data["output"]["stream"])
            self.assertEqual("hello stream", data["output"]["content"])
            self.assertEqual(5, data["output"]["chunk_count"])
            self.assertEqual(1, len(data["retrieval"]))
            self.assertEqual(60, data["context_budget"]["final_tokens_est"])
            self.assertTrue(any(x.get("source") == "stream" for x in data["tool_calls"]))
            self.assertTrue(any(x["type"] == "assistant_output" for x in data["timeline"]))
            diagnostics = {x["check"]: x for x in data["diagnostics"]}
            self.assertEqual("ok", diagnostics["run_status"]["status"])
            self.assertEqual("ok", diagnostics["context_budget"]["status"])

    def test_stream_runtime_writes_error_artifact_and_reraises(self):
        class BrokenStreamRunner:
            def run_stream(self, **_kwargs):
                yield "partial"
                raise RuntimeError("stream boom")

        with tempfile.TemporaryDirectory() as tmp:
            runtime = HarnessRuntime(BrokenStreamRunner(), artifact_store=ArtifactStore(tmp))
            with self.assertRaises(RuntimeError):
                list(
                    runtime.run_stream(
                        course_name="course",
                        mode="learn",
                        user_message="hello",
                        history=[],
                        request_id="req-stream-2",
                    )
                )

            data = ArtifactStore.read(runtime.last_artifact_path)
            self.assertEqual("failed", data["session"]["status"])
            self.assertEqual("partial", data["output"]["content"])
            self.assertEqual("RuntimeError", data["error"]["type"])
            self.assertIn("stream boom", data["error"]["message"])


if __name__ == "__main__":
    unittest.main()
