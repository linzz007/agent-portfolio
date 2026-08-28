"""最小对话入口。

示例：
    py -3 run_chat.py "你好"
    py -3 run_chat.py "帮我生成今日新闻分析报告"
    py -3 run_chat.py "帮我生成今日新闻分析报告" --show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from insight_engine.harness.env import load_project_env  # noqa: E402

load_project_env(PROJECT_ROOT)

from insight_engine.conversation.router import handle_message  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily AI Insight Engine 对话入口")
    parser.add_argument("message", nargs="?", default="你好", help="用户消息")
    parser.add_argument("--show", action="store_true", help="如果触发日报 Skill，则展示完整流程摘要")
    args = parser.parse_args()

    response = handle_message(args.message, show_summary=args.show)
    print(response.message)


if __name__ == "__main__":
    main()
