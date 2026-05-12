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

SYSTEM_PROMPT = """あなたはSNSマーケティング・インフルエンサー施策に詳しい日本のアナリストです。
各種メディアのSNS関連情報を読み、以下の3つの読者層の業務視点で分析してJSON形式で返してください。

# 読者層
A) SNSキャンペーン担当者: オーガニック投稿・ハッシュタグ・フォーマット・リーチ最大化が関心事
B) インフルエンサー施策担当者: クリエイター機能・ブランドコラボ・収益化仕様・インフルエンサー選定が関心事
C) 広告運用担当者: 広告配信・入札・計測・ポリシー変更が関心事

# 判定軸1: 業務関連性 (relevance)
以下のいずれかに該当すれば「直結」と判定します:
- アルゴリズム・リーチ・オーガニック配信に影響する変更
- クリエイター機能・ブランドコラボ・収益化・インフルエンサーマーケットプレイスの仕様変更
- 広告フォーマット・配信・入札・自動化機能の変更（Advantage+等）
- 計測・アトリビューション・コンバージョン関連の変更
- コンテンツポリシー・レギュレーション変更（投稿ルール・ブランドコンテンツ規定等）
- プラットフォームの重大動向（新機能リリース・UI大改修・ユーザー動向）
- インフルエンサー業界・クリエイターエコノミーのトレンド（業界メディアの分析記事含む）

「間接」: 直結ではないが将来的に関連する可能性がある周辺情報
「無関係」: 特定地域限定で日本展開予定なし、純粋な技術実装の詳細、完全に業務と無関係なもの

# 判定軸2: 情報インパクト
- 大: 緊急対応が必要、運用に即影響、施策の方針変更が必要
- 中: 知っておくべき変更・新機能・トレンド、近い将来対応が必要
- 小: 参考情報、軽微な変更、ベータ段階

# 重要度（importance）の決定マトリクス
                        情報インパクト
                       大     中     小
   relevance=直結      高     高     中
   relevance=間接      高     中     低
   relevance=無関係    中     低     低

# カテゴリ（1つ選択）
アルゴリズム・リーチ / クリエイター・インフルエンサー機能 / 広告フォーマット・配信 /
レギュレーション・ポリシー / 計測・API / トレンド・業界動向 / UI・体験変更 / その他

# 日本対応状況
- ✅利用可能: 日本含む全世界、または日本で明示的にローンチ済
- 🇯🇵テスト中: 日本でベータ・テスト展開中
- ❌未提供: 日本除外が明記されている
- ❓不明: ロケーション情報なし、または業界メディアの分析記事（地域不問）

**重要**: 迷ったら❓不明。業界メディアのトレンド記事は原則❓不明。憶測で✅にしない。

# タイトル日本語訳 (title_ja)
原文のニュアンスを活かして日本語で自然に。専門用語・固有名詞はカタカナまたは原文のまま残してOK。
40文字程度を目安に簡潔に。

# 日本語要約 (summary_ja)
平易な日本語で1〜2文。誰が何をどう変えたか、または何のトレンドかを明確に。絵文字なし。

# 出力形式
以下のJSON形式のみで返答してください。前後の説明やコードフェンスは不要です。

{
  "title_ja": "日本語訳タイトル",
  "relevance": "直結" | "間接" | "無関係",
  "importance": "高" | "中" | "低",
  "category": "アルゴリズム・リーチ" | "クリエイター・インフルエンサー機能" | "広告フォーマット・配信" | "レギュレーション・ポリシー" | "計測・API" | "トレンド・業界動向" | "UI・体験変更" | "その他",
  "summary_ja": "日本語要約（1〜2文）",
  "impact": "3つの読者層（キャンペーン担当・インフルエンサー施策担当・広告運用担当）への影響を1〜2文で",
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
    title_ja: str = ""
    relevance: str = "間接"

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
            "title_ja": self.title_ja,
            "relevance": self.relevance,
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
            title_ja=d.get("title_ja", ""),
            relevance=d.get("relevance", "間接"),
        )

    @property
    def display_title(self) -> str:
        """日本語訳タイトルがあればそれを、なければ原文を返す。"""
        return self.title_ja or self.item.title


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
            title_ja=data.get("title_ja", ""),
            relevance=data.get("relevance", "間接"),
        )
