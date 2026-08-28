"""运行完整 daily_news_report_skill，并输出可检查的流程总览。"""

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

from insight_engine.skill_executors.daily_news_report import (  # noqa: E402
    format_daily_news_report_result,
    run_daily_news_report_skill,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行完整 Daily AI Insight Engine Skill")
    parser.add_argument("--show", action="store_true", help="运行结束后直接打印 pipeline_summary.md")
    args = parser.parse_args()

    result = run_daily_news_report_skill()
    print("=== Daily AI Insight Engine Pipeline ===")
    print(format_daily_news_report_result(result, include_summary=args.show))


if __name__ == "__main__":
    main()
