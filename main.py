"""SNS News Monitor CLI エントリポイント。

Usage:
    python main.py check    # 速報チェック（1日2回）: 新着取得 + 重要度「高」を即時通知
    python main.py daily    # 日次ダイジェスト（夕方1回）: 過去24時間の「高」+「中」をまとめて通知
    python main.py weekly   # 週次レポート（月曜朝）: 先週分全件レポート
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

    # 速報通知: relevance=直結 かつ importance=高 のみ
    # （2軸ロジック化により、importance=高は業務関連性も担保された状態）
    urgent = [a for a in analyzed if a.importance == "高" and a.relevance != "無関係"]
    if urgent:
        logger.info(f"sending urgent notification for {len(urgent)} items")
        notifier.notify(urgent, mode="urgent")
    else:
        logger.info("no urgent items")

    storage.append_buffer(analyzed)
    storage.mark_seen([a.item.url for a in analyzed])

    # 直近7日分のバッファをダッシュボードHTMLに書き出し（無関係は除外）
    dashboard_items = [a for a in storage.get_weekly_items() if a.relevance != "無関係"]
    if dashboard_items:
        notifier.write_html_report(
            dashboard_items,
            "dashboard.html",
            f"SNS Update Dashboard ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            group_by_platform=True,
        )

    logger.info("=== check mode complete ===")


def cmd_daily() -> None:
    logger.info("=== daily mode start ===")
    storage = Storage()
    notifier = Notifier()

    all_items = storage.get_daily_items()
    items = [a for a in all_items if a.relevance != "無関係" and a.importance in ("高", "中")]
    logger.info(f"{len(items)}/{len(all_items)} items in past 24h (高+中, filtered)")

    if not items:
        logger.info("no items to notify, exiting")
        return

    notifier.notify(items, mode="daily")
    logger.info("=== daily mode complete ===")


def cmd_weekly() -> None:
    logger.info("=== weekly mode start ===")
    storage = Storage()
    notifier = Notifier()

    all_items = storage.get_weekly_items()
    items = [a for a in all_items if a.relevance != "無関係"]
    logger.info(f"{len(items)}/{len(all_items)} items in past 7 days (filtered)")

    if not items:
        logger.info("no items to report, exiting")
        return

    # Google Slides生成（CA-API経由）→ URLをSlack通知に埋め込む
    slides_url = notifier.create_weekly_slides(items)
    if slides_url:
        logger.info(f"slides created: {slides_url}")
    else:
        logger.warning("slides作成失敗 - URLなしで通知")

    notifier.notify(items, mode="weekly", slides_url=slides_url or "")

    today = datetime.now().strftime("%Y-%m-%d")
    notifier.write_html_report(
        items,
        f"weekly_{today}.html",
        f"週次SNSアップデートレポート ({today})",
        group_by_platform=True,
    )

    logger.info("=== weekly mode complete ===")


def cmd_test_dm() -> None:
    """検証用: バッファ内の重要度最上位3件をurgent扱いで通知（DM経路の疎通確認）。"""
    logger.info("=== test_dm mode start ===")
    storage = Storage()
    notifier = Notifier()

    items = storage.get_weekly_items()
    if not items:
        logger.info("no items in buffer, exiting")
        return

    from summarizer import AnalyzedItem  # type: ignore
    sorted_items = sorted(
        items,
        key=lambda a: ({"高": 0, "中": 1, "低": 2}.get(a.importance, 99),),
    )
    sample = sorted_items[:3]
    logger.info(f"sending test DM with {len(sample)} sample items")
    notifier.notify(sample, mode="urgent")
    logger.info("=== test_dm mode complete ===")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py [check|weekly|test_dm]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "check":
        cmd_check()
    elif mode == "daily":
        cmd_daily()
    elif mode == "weekly":
        cmd_weekly()
    elif mode == "test_dm":
        cmd_test_dm()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python main.py [check|daily|weekly|test_dm]")
        sys.exit(1)


if __name__ == "__main__":
    main()
