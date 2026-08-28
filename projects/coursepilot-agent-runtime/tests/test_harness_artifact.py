import tempfile
import unittest
from unittest import mock
from pathlib import Path

from core.harness.artifact import ArtifactStore, RunArtifact


class HarnessArtifactTests(unittest.TestCase):
    def test_artifact_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = RunArtifact(
                run_id="run_test",
                session={
                    "run_id": "run_test",
                    "started_at": "2026-05-06T10:00:00.000",
                    "status": "succeeded",
                },
                plan={"need_rag": True},
                output={"content": "ok"},
                metrics={"event_count": 1},
            )
            store = ArtifactStore(tmp)
            path = store.write(artifact)
            data = store.read(path)

            self.assertEqual("run_test", data["run_id"])
            self.assertEqual("harness.run_artifact.v3", data["schema_version"])
            self.assertEqual("ok", data["output"]["content"])
            self.assertEqual([], data["timeline"])
            self.assertEqual([], data["diagnostics"])

    def test_artifact_write_renders_html_when_env_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = RunArtifact(
                run_id="run_html",
                session={
                    "run_id": "run_html",
                    "started_at": "2026-05-06T10:00:00.000",
                    "status": "succeeded",
                    "mode": "learn",
                    "skill_id": "learn.answer.v1",
                    "user_message": "hello",
                },
                output={"content": "visible answer"},
                timeline=[{"type": "assistant_output", "status": "ok"}],
                diagnostics=[{"check": "run_status", "status": "ok", "message": "ok"}],
            )
            store = ArtifactStore(tmp)
            with mock.patch.dict("os.environ", {"HARNESS_RENDER_HTML": "1"}):
                path = store.write(artifact)

            html_path = Path(path).with_suffix(".html")
            self.assertTrue(html_path.exists())
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("RunArtifact run_html", html)
            self.assertIn("visible answer", html)


if __name__ == "__main__":
    unittest.main()
