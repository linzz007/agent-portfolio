"""项目级 .env 加载工具。

本项目不强依赖 python-dotenv，因此这里实现一个足够小的加载器：

- 默认从项目根目录或当前工作目录向上寻找 `.env`。
- 只在环境变量尚未存在时写入，避免覆盖用户 shell 里显式设置的值。
- 不打印 secret，只返回加载到的 key 名称，方便调试。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_project_env(start: Path | str | None = None, *, override: bool = False) -> list[str]:
    """加载项目 `.env` 文件，返回成功加载的变量名。"""
    env_path = find_env_file(start)
    if env_path is None:
        return []

    loaded: list[str] = []
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def find_env_file(start: Path | str | None = None) -> Path | None:
    """从指定目录向上寻找 `.env`。"""
    current = Path(start).resolve() if start is not None else Path.cwd().resolve()
    if current.is_file():
        current = current.parent

    for directory in [current, *current.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def parse_env_line(raw_line: str) -> tuple[str, str] | None:
    """解析一行 `.env`。"""
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value
