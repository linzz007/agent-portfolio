from __future__ import annotations

import os

from run_policy_api import load_local_env


def test_run_policy_api_loads_env_and_local_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("POLICY_IMPACT_API_PORT", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ANTHROPIC_AUTH_TOKEN=base-token",
                "ANTHROPIC_MODEL=wrong-model",
                "POLICY_IMPACT_API_PORT=8501",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "ANTHROPIC_MODEL=deepseek-v4-flash",
                'POLICY_IMPACT_API_PORT="8502"',
            ]
        ),
        encoding="utf-8",
    )

    load_local_env(tmp_path)

    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "base-token"
    assert os.environ["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert os.environ["POLICY_IMPACT_API_PORT"] == "8502"


def test_run_policy_api_ignores_blank_env_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "shell-token")
    (tmp_path / ".env.local").write_text(
        "ANTHROPIC_AUTH_TOKEN=\nANTHROPIC_MODEL=deepseek-v4-flash\n",
        encoding="utf-8",
    )

    load_local_env(tmp_path)

    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "shell-token"
    assert os.environ["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
