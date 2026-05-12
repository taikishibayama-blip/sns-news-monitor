"""既読URL管理 / 週次レポート用バッファの永続化モジュール。"""
import json
import os
import logging
from datetime import datetime, timedelta
from typing import List
from collections import OrderedDict

from fetcher import NewsItem
from summarizer import AnalyzedItem

logger = logging.getLogger(__name__)

SEEN_FILE = "seen.json"
BUFFER_FILE = "weekly_buffer.json"
MAX_SEEN = 10000
BUFFER_RETENTION_DAYS = 8
WEEKLY_WINDOW_DAYS = 7


class Storage:
    def __init__(self) -> None:
        self.seen: "OrderedDict[str, str]" = self._load_seen()
        self.buffer: List[dict] = self._load_buffer()

    @staticmethod
    def _load_seen() -> "OrderedDict[str, str]":
        if not os.path.exists(SEEN_FILE):
            return OrderedDict()
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return OrderedDict((url, ts) for url, ts in data.items())
        except Exception as e:
            logger.error(f"failed to load {SEEN_FILE}: {e}")
            return OrderedDict()

    @staticmethod
    def _load_buffer() -> List[dict]:
        if not os.path.exists(BUFFER_FILE):
            return []
        try:
            with open(BUFFER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"failed to load {BUFFER_FILE}: {e}")
            return []

    def filter_unseen(self, items: List[NewsItem]) -> List[NewsItem]:
        return [i for i in items if i.url and i.url not in self.seen]

    def mark_seen(self, urls: List[str]) -> None:
        now = datetime.now().isoformat()
        for url in urls:
            if url in self.seen:
                # キーの順序を最新化
                del self.seen[url]
            self.seen[url] = now
        # 上限超過時は古いものから削除
        while len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)
        try:
            with open(SEEN_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(self.seen), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"failed to save {SEEN_FILE}: {e}")

    def append_buffer(self, items: List[AnalyzedItem]) -> None:
        cutoff = datetime.now() - timedelta(days=BUFFER_RETENTION_DAYS)
        kept: List[dict] = []
        for entry in self.buffer:
            try:
                detected = datetime.fromisoformat(entry.get("detected_at", ""))
                if detected >= cutoff:
                    kept.append(entry)
            except Exception:
                continue
        kept.extend([i.to_dict() for i in items])
        self.buffer = kept
        try:
            with open(BUFFER_FILE, "w", encoding="utf-8") as f:
                json.dump(self.buffer, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"failed to save {BUFFER_FILE}: {e}")

    def get_daily_items(self) -> List[AnalyzedItem]:
        cutoff = datetime.now() - timedelta(hours=24)
        result: List[AnalyzedItem] = []
        for entry in self.buffer:
            try:
                detected = datetime.fromisoformat(entry.get("detected_at", ""))
                if detected >= cutoff:
                    result.append(AnalyzedItem.from_dict(entry))
            except Exception as e:
                logger.error(f"failed to parse buffer entry: {e}")
        return result

    def get_weekly_items(self) -> List[AnalyzedItem]:
        cutoff = datetime.now() - timedelta(days=WEEKLY_WINDOW_DAYS)
        result: List[AnalyzedItem] = []
        for entry in self.buffer:
            try:
                detected = datetime.fromisoformat(entry.get("detected_at", ""))
                if detected >= cutoff:
                    result.append(AnalyzedItem.from_dict(entry))
            except Exception as e:
                logger.error(f"failed to parse buffer entry: {e}")
        return result

    def clear_buffer(self) -> None:
        self.buffer = []
        if os.path.exists(BUFFER_FILE):
            try:
                os.remove(BUFFER_FILE)
            except Exception as e:
                logger.error(f"failed to remove {BUFFER_FILE}: {e}")
