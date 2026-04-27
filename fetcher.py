"""RSS / HTML スクレイピングによるニュース取得モジュール。"""
import logging
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_SOURCES = 2

# ナビゲーション・目次等のノイズタイトル（小文字部分一致）。
# Google Docsの "Page Summary" や類似のTOC要素を弾くため。
# 過剰除外を避けるため明らかに無関係なものだけ列挙する。
NOISE_TITLE_PATTERNS = [
    "page summary",
    "page contents",
    "table of contents",
    "on this page",
    "key takeaways",
    "目次",
]
MIN_TITLE_LEN = 4


def _is_noise_title(title: str) -> bool:
    if len(title) < MIN_TITLE_LEN:
        return True
    lower = title.lower()
    return any(p in lower for p in NOISE_TITLE_PATTERNS)


@dataclass
class NewsItem:
    platform: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        return cls(
            platform=d.get("platform", ""),
            source_name=d.get("source_name", ""),
            title=d.get("title", ""),
            url=d.get("url", ""),
            summary=d.get("summary", ""),
            published=d.get("published", ""),
        )


def fetch_rss(source: Dict[str, Any]) -> List[NewsItem]:
    name = source["name"]
    logger.info(f"fetching RSS: {name}")
    items: List[NewsItem] = []
    try:
        feed = feedparser.parse(source["url"], request_headers={"User-Agent": USER_AGENT})
        for entry in feed.entries[:20]:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue
            items.append(
                NewsItem(
                    platform=source["platform"],
                    source_name=name,
                    title=title,
                    url=url,
                    summary=(entry.get("summary") or "")[:500],
                    published=entry.get("published", ""),
                )
            )
    except Exception as e:
        logger.error(f"RSS fetch failed for {name}: {e}")
    logger.info(f"  -> {len(items)} items")
    return items


def fetch_html(source: Dict[str, Any]) -> List[NewsItem]:
    name = source["name"]
    logger.info(f"fetching HTML: {name}")
    items: List[NewsItem] = []
    seen_urls = set()
    try:
        resp = requests.get(
            source["url"],
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        elements = soup.select(source["item_selector"])
        title_sel = source.get("title_selector") or ""
        link_sel = source.get("link_selector") or ""
        base = source.get("base_url", source["url"])

        for el in elements[:30]:
            title_el = el.select_one(title_sel) if title_sel else el
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title or _is_noise_title(title):
                continue

            # href抽出: link_selector指定 → 子aタグ → 自身のid属性（アンカー） の順にフォールバック
            href = ""
            if link_sel:
                link_el = el.select_one(link_sel)
                if link_el and hasattr(link_el, "get"):
                    href = link_el.get("href", "") or ""
            elif el.name == "a":
                href = el.get("href", "") or ""
            else:
                a_tag = el.find("a")
                if a_tag:
                    href = a_tag.get("href", "") or ""

            # フォールバック: 要素自身のid属性をアンカーとして利用
            # （見出しのみが並ぶGoogle Docs等の構造で必要）
            if not href:
                el_id = el.get("id", "") if hasattr(el, "get") else ""
                if el_id:
                    href = f"#{el_id}"

            if not href:
                continue

            url = urljoin(base, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            items.append(
                NewsItem(
                    platform=source["platform"],
                    source_name=name,
                    title=title,
                    url=url,
                )
            )
    except Exception as e:
        logger.error(f"HTML fetch failed for {name}: {e}")
    logger.info(f"  -> {len(items)} items")
    return items


def fetch_all(sources: List[Dict[str, Any]]) -> List[NewsItem]:
    all_items: List[NewsItem] = []
    for source in sources:
        stype = source.get("type")
        if stype == "rss":
            all_items.extend(fetch_rss(source))
        elif stype == "html":
            all_items.extend(fetch_html(source))
        else:
            logger.warning(f"unknown source type: {stype}")
        time.sleep(SLEEP_BETWEEN_SOURCES)
    return all_items
