"""SNS News Monitor CLI エントリポイント。

Usage:
    python main.py check    # 速報チェック（3時間おき想定）
    python main.py weekly   # 週次レポート（金曜17:00 JST想定）
"""
import sys
import logging
from datetime import datetime

import yaml
from dotenv import load_dotenv

from fetcher import fetch_all
from summarizer import Summarizer
from notifier import Notifier
from storage import Storage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

CONFIG_PATH = "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_check() -> None:
    logger.info("=== check mode start ===")
    config = load_config()
    storage = Storage()
    summarizer = Summarizer()
    notifier = Notifier()

    items = fetch_all(config["sources"])
    logger.info(f"fetched {len(items)} items total")

    new_items = storage.filter_unseen(items)
    logger.info(f"{len(new_items)} new items after dedup")

    if not new_items:
        logger.info("no new items, exiting")
        return

    analyzed = []
    for item in new_items:
        result = summarizer.analyze(item)
        if result is not None:
            analyzed.append(result)

    logger.info(f"analyzed {len(analyzed)} items")

    urgent = [a for a in analyzed if a.importance == "高"]
    if urgent:
        logger.info(f"sending urgent notification for {len(urgent)} items")
        notifier.notify(urgent, mode="urgent")
    else:
        logger.info("no urgent items")

    storage.append_buffer(analyzed)
    storage.mark_seen([a.item.url for a in analyzed])

    # 直近7日分のバッファをダッシュボードHTMLに書き出し
    dashboard_items = storage.get_weekly_items()
    if dashboard_items:
        notifier.write_html_report(
            dashboard_items,
            "dashboard.html",
            f"SNS Update Dashboard ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            group_by_platform=True,
        )

    logger.info("=== check mode complete ===")


def cmd_weekly() -> None:
    logger.info("=== weekly mode start ===")
    storage = Storage()
    notifier = Notifier()

    items = storage.get_weekly_items()
    logger.info(f"{len(items)} items in past 7 days")

    if not items:
        logger.info("no items to report, exiting")
        return

    notifier.notify(items, mode="weekly")

    # 週次レポートを日付付きファイルでアーカイブ
    today = datetime.now().strftime("%Y-%m-%d")
    notifier.write_html_report(
        items,
        f"weekly_{today}.html",
        f"週次SNSアップデートレポート ({today})",
        group_by_platform=True,
    )

    logger.info("=== weekly mode complete ===")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py [check|weekly]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "check":
        cmd_check()
    elif mode == "weekly":
        cmd_weekly()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python main.py [check|weekly]")
        sys.exit(1)


if __name__ == "__main__":
    main()
