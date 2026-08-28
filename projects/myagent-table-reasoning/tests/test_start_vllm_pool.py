from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "scripts" / "server" / "start_vllm_pool.sh"


class StartVllmPoolTests(unittest.TestCase):
    def test_background_vllm_is_started_noninteractive_safe(self):
        script = START_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("setsid", script)
        self.assertIn("nohup", script)
        self.assertIn("< /dev/null", script)

    def test_stale_pid_does_not_restart_over_live_port(self):
        script = START_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("port_is_listening", script)
        self.assertIn("already has a listener", script)
        self.assertIn("stale pid", script)


if __name__ == "__main__":
    unittest.main()
