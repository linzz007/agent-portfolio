"""运行产物保存工具。

Harness 的每个阶段都应该留下可检查的文件。
这个模块只负责创建目录和写 JSON，不负责业务逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from insight_engine.harness.state import InsightEngineState


def project_root() -> Path:
    """返回项目根目录。

    当前文件位置是 `src/insight_engine/harness/artifacts.py`，
    所以向上 3 层是项目根目录。
    """
    return Path(__file__).resolve().parents[3]


def ensure_run_dir(base_dir: str | Path, run_id: str) -> Path:
    """创建并返回某次运行的产物目录。"""
    base_path = Path(base_dir)
    if not base_path.is_absolute():
        base_path = project_root() / base_path

    run_dir = base_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json_artifact(
    state: InsightEngineState,
    artifact_name: str,
    data: Any,
    base_dir: str | Path,
    filename: str,
) -> Path:
    """保存 JSON 产物，并把路径记录到 State。"""
    run_dir = ensure_run_dir(base_dir=base_dir, run_id=state.run_id)
    output_path = run_dir / filename

    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state.add_artifact(artifact_name, str(output_path))
    return output_path

