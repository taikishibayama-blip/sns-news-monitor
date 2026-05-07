"""Slack / メール通知 + HTMLファイル出力モジュール。"""
import os
import smtplib
import logging
import html as html_lib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict

import requests

from summarizer import AnalyzedItem

REPORTS_DIR = "reports"
IMPORTANCE_ORDER = {"高": 0, "中": 1, "低": 2}
# 媒体グループの表示順（config.yamlのプラットフォーム値と対応）
PLATFORM_ORDER = ["Meta", "X", "TikTok", "YouTube", "Google Ads"]

# 社内CA-API（HENNGE One OIDC経由のToken管理API）
CA_API_BASE_DEFAULT = "https://ca-token-api-278149334715.asia-northeast1.run.app"
DASHBOARD_URL = "https://taikishibayama-blip.github.io/sns-news-monitor/"

logger = logging.getLogger(__name__)

IMPORTANCE_EMOJI = {
    "高": ":rotating_light:",
    "中": ":warning:",
    "低": ":information_source:",
}

SLACK_BLOCK_LIMIT = 45  # Slackは50/メッセージ上限。安全マージン込み


class Notifier:
    def __init__(self) -> None:
        self.slack_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587") or "587")
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "")
        self.mail_from = os.getenv("MAIL_FROM", "")
        self.mail_to = [a.strip() for a in os.getenv("MAIL_TO", "").split(",") if a.strip()]
        # CA-API経由のSlack DM送信（USER_EMAILが設定されている場合に利用）
        self.user_email = os.getenv("USER_EMAIL", "")
        self.ca_api_base = os.getenv("CA_API_BASE", CA_API_BASE_DEFAULT)

    def notify(self, items: List[AnalyzedItem], mode: str) -> None:
        if not items:
            logger.info("no items to notify")
            return
        if mode == "urgent":
            header = f":rotating_light: 重要アップデート速報 ({len(items)}件)"
            subject = f"[速報] SNS重要アップデート ({len(items)}件)"
            group = False
        elif mode == "weekly":
            header = f":calendar: 週次SNSアップデートレポート ({len(items)}件)"
            subject = f"[週次] SNSアップデートレポート ({len(items)}件)"
            group = True
        else:
            logger.warning(f"unknown notify mode: {mode}")
            return

        # Slack配信は CA-API経由DM > Webhook の優先順
        if self.user_email:
            self._send_slack_dm_via_ca_api(items, header_text=header, group_by_platform=group)
        elif self.slack_url:
            self._send_slack(items, header_text=header, group_by_platform=group)
        self._send_email(items, subject=subject, group_by_platform=group)

    # ----- Slack -----

    def _build_item_blocks(self, a: AnalyzedItem) -> list:
        """1件あたり2ブロックのコンパクト表示。
        header（重要度+プラットフォーム+日本語タイトル）+ section（要約+メタ+出典）。
        """
        imp_e = IMPORTANCE_EMOJI.get(a.importance, "")
        title = a.display_title
        title_short = title[:120] + ("…" if len(title) > 120 else "")
        detected = a.detected_at[5:16].replace("T", " ") if len(a.detected_at) >= 16 else a.detected_at
        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{imp_e}【{a.importance}】{a.item.platform}｜{title_short}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{a.summary_ja}\n"
                        f"_{a.category} · {a.jp_status} · 検知 {detected}_  "
                        f"<{a.item.url}|出典: {a.item.source_name}>"
                    ),
                },
            },
        ]

    def _build_full_blocks(self, items: List[AnalyzedItem], header_text: str, group_by_platform: bool) -> list:
        """Slack送信用の完全なBlock Kit構造を構築（Webhook/CA-API共用）。"""
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"<{DASHBOARD_URL}|📊 ダッシュボードを開く>"}},
            {"type": "divider"},
        ]
        if group_by_platform:
            grouped = self._group_by_platform(items)
            for platform, plat_items in grouped.items():
                blocks.append({
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"■ {platform} ({len(plat_items)}件)", "emoji": True},
                })
                for a in plat_items:
                    blocks.extend(self._build_item_blocks(a))
        else:
            for a in items:
                blocks.extend(self._build_item_blocks(a))
        return blocks

    def _send_slack(self, items: List[AnalyzedItem], header_text: str, group_by_platform: bool) -> None:
        if not self.slack_url:
            logger.warning("SLACK_WEBHOOK_URL not set, skipping Slack webhook")
            return
        blocks = self._build_full_blocks(items, header_text, group_by_platform)
        for i in range(0, len(blocks), SLACK_BLOCK_LIMIT):
            chunk = blocks[i : i + SLACK_BLOCK_LIMIT]
            try:
                resp = requests.post(self.slack_url, json={"blocks": chunk}, timeout=30)
                resp.raise_for_status()
                logger.info(f"slack webhook chunk sent: {len(chunk)} blocks")
            except Exception as e:
                logger.error(f"slack webhook post failed: {e}")

    # ----- Slack DM via CA-API（社内HENNGE One認証）-----

    def _get_slack_token_via_ca_api(self) -> str:
        """CA-API経由で Slack OAuth Token を取得。"""
        res = requests.post(
            f"{self.ca_api_base}/token/hennge",
            json={"email": self.user_email},
            timeout=15,
        )
        if res.status_code == 404:
            raise RuntimeError(f"CA-API初回認証が未完了。ブラウザで {self.ca_api_base}/auth を開いてください")
        res.raise_for_status()
        hennge_token = res.json()["access_token"]

        res = requests.get(
            f"{self.ca_api_base}/token/slack",
            headers={"Authorization": f"Bearer {hennge_token}"},
            timeout=15,
        )
        res.raise_for_status()
        return res.json()["access_token"]

    def _send_slack_dm_via_ca_api(self, items: List[AnalyzedItem], header_text: str, group_by_platform: bool) -> None:
        """CA-API経由で Slack DM を社長宛に送信。"""
        try:
            token = self._get_slack_token_via_ca_api()
        except Exception as e:
            logger.error(f"CA-API token取得失敗: {e}")
            return

        # 自分のSlack User IDを auth.test で取得
        try:
            auth_resp = requests.get(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            ).json()
            if not auth_resp.get("ok"):
                logger.error(f"slack auth.test failed: {auth_resp.get('error')}")
                return
            user_id = auth_resp["user_id"]
        except Exception as e:
            logger.error(f"slack auth.test error: {e}")
            return

        blocks = self._build_full_blocks(items, header_text, group_by_platform)
        for i in range(0, len(blocks), SLACK_BLOCK_LIMIT):
            chunk = blocks[i : i + SLACK_BLOCK_LIMIT]
            try:
                resp = requests.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={"channel": user_id, "text": header_text, "blocks": chunk},
                    timeout=30,
                ).json()
                if not resp.get("ok"):
                    logger.error(f"slack chat.postMessage failed: {resp.get('error')}")
                else:
                    logger.info(f"slack DM chunk sent: {len(chunk)} blocks")
            except Exception as e:
                logger.error(f"slack DM post failed: {e}")

    # ----- HTML レンダリング共通（メール本文・ファイル出力で共有）-----

    @staticmethod
    def _group_by_platform(items: List[AnalyzedItem]) -> Dict[str, List[AnalyzedItem]]:
        """媒体別にグループ化。各グループ内は重要度順（高→中→低）→検知時刻新しい順。"""
        grouped: Dict[str, List[AnalyzedItem]] = {}
        for a in items:
            grouped.setdefault(a.item.platform, []).append(a)
        # 各グループ内をソート
        for platform in grouped:
            grouped[platform].sort(
                key=lambda a: (IMPORTANCE_ORDER.get(a.importance, 99), -Notifier._detected_ts(a))
            )
        # 媒体グループを既定の順序で並べ直す（未知の媒体は末尾）
        ordered: Dict[str, List[AnalyzedItem]] = {}
        for p in PLATFORM_ORDER:
            if p in grouped:
                ordered[p] = grouped[p]
        for p, items_list in grouped.items():
            if p not in ordered:
                ordered[p] = items_list
        return ordered

    @staticmethod
    def _imp_class(imp: str) -> str:
        return {"高": "high", "中": "medium", "低": "low"}.get(imp, "")

    def _render_item_html(self, a: AnalyzedItem) -> str:
        e = html_lib.escape
        original_title = "" if a.display_title == a.item.title else f'<div class="orig-title">{e(a.item.title)}</div>'
        return f"""
        <div class="item {self._imp_class(a.importance)}">
            <div class="meta">[{e(a.importance)}] {e(a.item.platform)} / {e(a.category)} / 検知: {e(a.detected_at[:19])}</div>
            <div class="title">{e(a.display_title)}</div>
            {original_title}
            <div class="summary">{e(a.summary_ja)}</div>
            <div class="impact"><b>影響:</b> {e(a.impact)}</div>
            <div class="jp"><b>日本対応:</b> {e(a.jp_status)} - {e(a.jp_note)}</div>
            <div><a href="{e(a.item.url)}">出典: {e(a.item.source_name)}</a></div>
        </div>
        """

    def _build_email_html(self, items: List[AnalyzedItem], title: str, group_by_platform: bool) -> str:
        css = """
        <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif; max-width: 800px; margin: 20px auto; padding: 0 20px; color: #333; }
        h1 { color: #1a1a1a; border-bottom: 2px solid #4a90e2; padding-bottom: 8px; }
        h2 { color: #4a90e2; margin-top: 32px; border-left: 4px solid #4a90e2; padding-left: 8px; }
        .item { border-left: 4px solid #ccc; padding: 12px 16px; margin: 16px 0; background: #fafafa; border-radius: 4px; }
        .item.high { border-left-color: #e74c3c; background: #fdecec; }
        .item.medium { border-left-color: #f39c12; background: #fef5e7; }
        .item.low { border-left-color: #95a5a6; }
        .meta { font-size: 12px; color: #666; margin-bottom: 8px; }
        .title { font-size: 16px; font-weight: bold; margin-bottom: 4px; }
        .orig-title { font-size: 12px; color: #888; margin-bottom: 8px; font-style: italic; }
        .summary { margin: 8px 0; line-height: 1.6; }
        .impact { background: #fff3cd; padding: 8px; border-radius: 4px; margin: 8px 0; }
        .jp { background: #e8f4f8; padding: 8px; border-radius: 4px; margin: 8px 0; font-size: 13px; }
        a { color: #4a90e2; }
        </style>
        """

        body_html = ""
        if group_by_platform:
            for platform, plat_items in self._group_by_platform(items).items():
                body_html += f"<h2>{html_lib.escape(platform)} ({len(plat_items)}件)</h2>"
                for a in plat_items:
                    body_html += self._render_item_html(a)
        else:
            for a in items:
                body_html += self._render_item_html(a)

        return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>{html_lib.escape(title)}</title>{css}</head>
<body>
<h1>{html_lib.escape(title)}</h1>
<p>合計: {len(items)}件</p>
{body_html}
</body>
</html>"""

    # ----- HTMLファイル出力（タブUI付きダッシュボード）-----

    def write_html_report(
        self,
        items: List[AnalyzedItem],
        file_name: str,
        title: str,
        group_by_platform: bool,
    ) -> str:
        """HTMLレポートをreports/配下に書き出して、書き出したパスを返す。"""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        if group_by_platform and items:
            html = self._build_dashboard_html(items, title)
        else:
            sorted_items = sorted(
                items,
                key=lambda a: (IMPORTANCE_ORDER.get(a.importance, 99), -self._detected_ts(a)),
            )
            html = self._build_email_html(sorted_items, title, group_by_platform)
        path = os.path.join(REPORTS_DIR, file_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"report written: {path}")
        return path

    def _build_dashboard_html(self, items: List[AnalyzedItem], title: str) -> str:
        """媒体タブ切替式のダッシュボードHTML。各タブ内は重要度順。"""
        e = html_lib.escape
        grouped = self._group_by_platform(items)

        css = """
        <style>
        :root { --primary: #4a90e2; --bg: #f5f7fa; --card: #fff; }
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif; margin: 0; background: var(--bg); color: #333; }
        .container { max-width: 960px; margin: 0 auto; padding: 24px; }
        h1 { color: #1a1a1a; border-bottom: 2px solid var(--primary); padding-bottom: 12px; margin-top: 0; }
        .summary { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 24px; }
        .summary-card { flex: 1 1 140px; background: var(--card); padding: 12px 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .summary-card .label { font-size: 12px; color: #666; }
        .summary-card .count { font-size: 24px; font-weight: bold; color: var(--primary); }
        .summary-card .breakdown { font-size: 11px; color: #888; margin-top: 4px; }
        .tabs { display: flex; gap: 4px; border-bottom: 2px solid #ddd; margin-bottom: 16px; flex-wrap: wrap; }
        .tab-btn { background: none; border: none; padding: 12px 20px; cursor: pointer; font-size: 14px; font-weight: 600; color: #666; border-bottom: 3px solid transparent; margin-bottom: -2px; transition: all 0.15s; }
        .tab-btn:hover { color: var(--primary); background: #eef3fa; }
        .tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
        .tab-btn .badge { display: inline-block; background: #ddd; color: #555; border-radius: 10px; font-size: 11px; padding: 1px 8px; margin-left: 6px; }
        .tab-btn.active .badge { background: var(--primary); color: white; }
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }
        .item { background: var(--card); border-left: 4px solid #ccc; padding: 14px 18px; margin: 12px 0; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
        .item.high { border-left-color: #e74c3c; background: #fdecec; }
        .item.medium { border-left-color: #f39c12; background: #fef9ee; }
        .item.low { border-left-color: #95a5a6; }
        .meta { font-size: 11px; color: #888; margin-bottom: 6px; }
        .badge-imp { display: inline-block; padding: 2px 8px; border-radius: 4px; font-weight: bold; margin-right: 6px; font-size: 11px; }
        .badge-imp.high { background: #e74c3c; color: white; }
        .badge-imp.medium { background: #f39c12; color: white; }
        .badge-imp.low { background: #95a5a6; color: white; }
        .title-row { font-size: 15px; font-weight: bold; margin-bottom: 4px; line-height: 1.4; }
        .orig-title-row { font-size: 11px; color: #999; margin-bottom: 8px; font-style: italic; line-height: 1.4; }
        .summary-text { margin: 8px 0; line-height: 1.7; font-size: 14px; }
        .impact { background: #fff7e0; padding: 8px 12px; border-radius: 4px; margin: 8px 0; font-size: 13px; }
        .jp { background: #e8f4f8; padding: 8px 12px; border-radius: 4px; margin: 8px 0; font-size: 12px; }
        .source-link { font-size: 12px; }
        .source-link a { color: var(--primary); text-decoration: none; }
        .source-link a:hover { text-decoration: underline; }
        .empty { color: #999; padding: 40px; text-align: center; }
        </style>
        """

        js = """
        <script>
        function showTab(name) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.getElementById('btn-' + name).classList.add('active');
            document.getElementById('pane-' + name).classList.add('active');
        }
        </script>
        """

        # サマリーカード
        summary_cards = ""
        for platform, plat_items in grouped.items():
            from collections import Counter
            c = Counter(i.importance for i in plat_items)
            summary_cards += f"""
            <div class="summary-card">
                <div class="label">{e(platform)}</div>
                <div class="count">{len(plat_items)}</div>
                <div class="breakdown">高{c.get('高', 0)} / 中{c.get('中', 0)} / 低{c.get('低', 0)}</div>
            </div>
            """

        # タブボタン
        tabs_html = '<div class="tabs">'
        for idx, (platform, plat_items) in enumerate(grouped.items()):
            slug = self._slug(platform)
            active = "active" if idx == 0 else ""
            tabs_html += (
                f'<button class="tab-btn {active}" id="btn-{slug}" onclick="showTab(\'{slug}\')">'
                f'{e(platform)}<span class="badge">{len(plat_items)}</span></button>'
            )
        tabs_html += "</div>"

        # 各タブのコンテンツ
        panes_html = ""
        for idx, (platform, plat_items) in enumerate(grouped.items()):
            slug = self._slug(platform)
            active = "active" if idx == 0 else ""
            inner = "".join(self._render_item_card(a) for a in plat_items)
            panes_html += f'<div class="tab-pane {active}" id="pane-{slug}">{inner}</div>'

        return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
{css}
</head>
<body>
<div class="container">
<h1>{e(title)}</h1>
<div class="summary">{summary_cards}</div>
{tabs_html}
{panes_html}
</div>
{js}
</body>
</html>"""

    @staticmethod
    def _slug(platform: str) -> str:
        return platform.lower().replace(" ", "-")

    def _render_item_card(self, a: AnalyzedItem) -> str:
        e = html_lib.escape
        original_title = "" if a.display_title == a.item.title else f'<div class="orig-title-row">{e(a.item.title)}</div>'
        return f"""
        <div class="item {self._imp_class(a.importance)}">
            <div class="meta">
                <span class="badge-imp {self._imp_class(a.importance)}">{e(a.importance)}</span>
                {e(a.category)} · 検知: {e(a.detected_at[:16].replace('T', ' '))}
            </div>
            <div class="title-row">{e(a.display_title)}</div>
            {original_title}
            <div class="summary-text">{e(a.summary_ja)}</div>
            <div class="impact"><b>影響:</b> {e(a.impact)}</div>
            <div class="jp"><b>日本対応:</b> {e(a.jp_status)} — {e(a.jp_note)}</div>
            <div class="source-link"><a href="{e(a.item.url)}" target="_blank">出典: {e(a.item.source_name)}</a></div>
        </div>
        """

    @staticmethod
    def _detected_ts(a: AnalyzedItem) -> float:
        try:
            return datetime.fromisoformat(a.detected_at).timestamp()
        except Exception:
            return 0.0

    # ----- Email -----

    def _send_email(self, items: List[AnalyzedItem], subject: str, group_by_platform: bool) -> None:
        if not self.smtp_host or not self.mail_to:
            logger.warning("SMTP not configured or MAIL_TO empty, skipping email")
            return

        html = self._build_email_html(items, subject, group_by_platform)
        msg = MIMEMultipart("alternative")
        msg["From"] = self.mail_from
        msg["To"] = ", ".join(self.mail_to)
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                if self.smtp_user and self.smtp_pass:
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
            logger.info(f"email sent: {subject}")
        except Exception as e:
            logger.error(f"email send failed: {e}")
