from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "server" / "healthcheck_openai_compatible.py"


class ModelListHandler(BaseHTTPRequestHandler):
    models = ["qwen/qwen3-32b"]
    auth_headers: list[str | None] = []

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        self.__class__.auth_headers.append(self.headers.get("Authorization"))
        payload = {"data": [{"id": model} for model in self.__class__.models]}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class HealthcheckOpenAICompatibleTests(unittest.TestCase):
    def setUp(self) -> None:
        ModelListHandler.models = ["qwen/qwen3-32b"]
        ModelListHandler.auth_headers = []
        self.server = HTTPServer(("127.0.0.1", 0), ModelListHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_healthcheck(self, *, model: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--api-base-url",
                self.base_url,
                "--model",
                model,
                "--api-key-env",
                "OPENROUTER_API_KEY",
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_healthcheck_verifies_key_endpoint_and_model_without_leaking_secret(self):
        """Catches API candidates reaching Gate-10 with only env-var existence checked."""
        env = {**os.environ, "OPENROUTER_API_KEY": "sk-test-secret"}

        completed = self.run_healthcheck(model="qwen/qwen3-32b", env=env)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("api healthcheck ok", completed.stdout)
        self.assertIn("qwen/qwen3-32b", completed.stdout)
        self.assertEqual(ModelListHandler.auth_headers, ["Bearer sk-test-secret"])
        self.assertNotIn("sk-test-secret", completed.stdout)
        self.assertNotIn("sk-test-secret", completed.stderr)

    def test_healthcheck_fails_when_target_model_is_not_listed(self):
        """Catches a wrong provider model name before any Gate rows are launched."""
        ModelListHandler.models = ["other/model"]
        env = {**os.environ, "OPENROUTER_API_KEY": "sk-test-secret"}

        completed = self.run_healthcheck(model="qwen/qwen3-32b", env=env)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("model not listed", completed.stderr)
        self.assertNotIn("sk-test-secret", completed.stderr)

    def test_healthcheck_fails_before_request_when_api_key_env_is_missing(self):
        """Catches missing API key variables without making a network request."""
        env = dict(os.environ)
        env.pop("OPENROUTER_API_KEY", None)

        completed = self.run_healthcheck(model="qwen/qwen3-32b", env=env)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing API key env: OPENROUTER_API_KEY", completed.stderr)
        self.assertEqual(ModelListHandler.auth_headers, [])


if __name__ == "__main__":
    unittest.main()
