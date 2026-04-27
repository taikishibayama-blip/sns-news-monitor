"""Claude APIによるニュース分析モジュール。"""
import os
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from anthropic import Anthropic

from fetcher import NewsItem

logger = logging.getLogger(__name__)

# 分析タスクは比較的軽量なため、コスト効率優先でHaikuをデフォルトに。
# 精度を上げたい場合は環境変数 CLAUDE_MODEL で claude-sonnet-4-6 等に切替可能。
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL = os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)
MAX_TOKENS = 1024

SYSTEM_PROMPT = """あなたはSNS広告運用に詳しい日本のアナリストです。
SNSプラットフォーム（Meta / X / TikTok / YouTube / Google Ads）の公式アップデート情報を読み、以下の観点で分析してJSON形式で返してください。

# 重要度の判定基準
- 高: 広告ポリシー変更、API破壊的変更、課金影響、緊急対応必要、運用が止まる/止まりうるレベル
- 中: 新機能追加、UI変更、メトリクス変更、知っておくべき仕様変更
- 低: 軽微な修正、既知機能のドキュメント更新、ベータ機能の小変更

# カテゴリ
広告ポリシー / API / 新機能 / UI変更 / ポリシー / 課金 / メトリクス / その他 のいずれか1つ

# 日本対応状況の判定
- ✅利用可能: 日本含む全世界 / 日本でも明示的にローンチ済
- 🇯🇵テスト中: 日本でベータ / テスト展開中
- ❌未提供: 米国のみ等、日本除外が明記されている
- ❓不明: ロケーション情報の記載がない、判断材料がない

**重要**: 迷ったら❓不明にすること。憶測で✅にしないでください。

# 日本語要約のルール
中学生でも分かる平易な日本語で2〜3文。専門用語を避ける。絵文字なし。

# 出力形式
以下のJSON形式のみで返答してください。前後の説明やコードフェンスは不要です。

{
  "importance": "高" | "中" | "低",
  "category": "広告ポリシー" | "API" | "新機能" | "UI変更" | "ポリシー" | "課金" | "メトリクス" | "その他",
  "summary_ja": "日本語要約（2〜3文）",
  "impact": "ユーザー・運用者への影響を1文で",
  "jp_status": "✅利用可能" | "🇯🇵テスト中" | "❌未提供" | "❓不明",
  "jp_note": "日本対応の根拠を簡潔に"
}"""


@dataclass
class AnalyzedItem:
    item: NewsItem
    importance: str
    category: str
    summary_ja: str
    impact: str
    jp_status: str
    jp_note: str
    detected_at: str

    def to_dict(self) -> dict:
        return {
            "item": self.item.to_dict(),
            "importance": self.importance,
            "category": self.category,
            "summary_ja": self.summary_ja,
            "impact": self.impact,
            "jp_status": self.jp_status,
            "jp_note": self.jp_note,
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnalyzedItem":
        return cls(
            item=NewsItem.from_dict(d["item"]),
            importance=d.get("importance", "低"),
            category=d.get("category", "その他"),
            summary_ja=d.get("summary_ja", ""),
            impact=d.get("impact", ""),
            jp_status=d.get("jp_status", "❓不明"),
            jp_note=d.get("jp_note", ""),
            detected_at=d.get("detected_at", datetime.now().isoformat()),
        )


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()
    return text


class Summarizer:
    def __init__(self) -> None:
        self.client = Anthropic()

    def analyze(self, item: NewsItem) -> Optional[AnalyzedItem]:
        user_content = (
            f"# 元情報\n"
            f"プラットフォーム: {item.platform}\n"
            f"ソース: {item.source_name}\n"
            f"タイトル: {item.title}\n"
            f"URL: {item.url}\n"
            f"要約: {item.summary}\n"
            f"公開日: {item.published}\n\n"
            f"上記をJSON形式で分析してください。"
        )

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return None

        try:
            text = response.content[0].text
            text = _strip_code_fences(text)
            data = json.loads(text)
        except Exception as e:
            logger.error(f"failed to parse Claude response: {e}")
            return None

        return AnalyzedItem(
            item=item,
            importance=data.get("importance", "低"),
            category=data.get("category", "その他"),
            summary_ja=data.get("summary_ja", ""),
            impact=data.get("impact", ""),
            jp_status=data.get("jp_status", "❓不明"),
            jp_note=data.get("jp_note", ""),
            detected_at=datetime.now().isoformat(),
        )
