"""Run the FastAPI service."""

from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if value == "":
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def load_local_env(root: Path = ROOT) -> None:
    _load_env_file(root / ".env")
    _load_env_file(root / ".env.local", override=True)


if __name__ == "__main__":
    load_local_env()
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "FastAPI service dependencies are not installed. Run `py -3 -m pip install -e .` first."
        ) from exc
    port = int(os.getenv("POLICY_IMPACT_API_PORT", "8501"))
    uvicorn.run("policy_impact.app.api:app", host="127.0.0.1", port=port, reload=False)
