# SNS News Monitor

主要SNSプラットフォーム（Meta / X / YouTube / Google Ads）の公式アップデート情報を自動取得し、Claude APIで日本語分析した上で、 **HTMLダッシュボードファイル** として書き出すPython CLIツール。

Slack/メール配信機能も実装済（環境変数を設定すれば動作）。

## 配信スケジュール

| モード | 実行頻度 | 内容 |
|---|---|---|
| `check`（速報） | 3時間おき | 新着取得 → Claude分析 → 全件バッファ蓄積 → `reports/dashboard.html` 更新 |
| `weekly`（週次） | 毎週金曜 17:00 JST | 過去7日分を `reports/weekly_YYYY-MM-DD.html` にアーカイブ生成 |

## 出力ファイル

| ファイル | 内容 | 更新タイミング |
|---|---|---|
| `reports/dashboard.html` | 直近7日のバッファ全件を重要度順表示 | check実行毎に更新 |
| `reports/weekly_YYYY-MM-DD.html` | 週次レポートのアーカイブ（日付付き） | weekly実行時に新規生成 |

ブラウザでダブルクリックすれば閲覧可能。GitHub上でも直接開けます。

## ディレクトリ構成

```
sns_news_monitor/
├── README.md
├── requirements.txt
├── config.yaml              # 監視ソース定義
├── .env.example
├── .gitignore
├── main.py                  # CLI エントリポイント
├── fetcher.py               # RSS / HTML 取得
├── summarizer.py            # Claude API 分析
├── notifier.py              # HTMLレポート生成 / Slack / メール送信
├── storage.py               # 既読管理 / 週次バッファ
├── reports/                 # 生成されたHTMLレポート（自動作成）
└── .github/workflows/monitor.yml   # GitHub Actions 自動実行
```

## セットアップ手順

### 1. ローカルで動作確認する場合

```bash
# 1. 仮想環境作成
python3 -m venv .venv
source .venv/bin/activate

# 2. 依存ライブラリインストール
pip install -r requirements.txt

# 3. 環境変数設定
cp .env.example .env
# .env を編集して各種APIキー等を入力

# 4. 速報モードで動作確認
python main.py check

# 5. 生成されたダッシュボードを開く
open reports/dashboard.html

# 6. 週次モード動作確認（バッファに蓄積後）
python main.py weekly
open reports/weekly_*.html
```

### 2. GitHub Actionsへのデプロイ

#### Step 1: GitHubリポジトリを作成

1. [GitHub](https://github.com) にログイン
2. 右上「+」→「New repository」
3. リポジトリ名を入力（例: `sns-news-monitor`）
4. **Private** を選択（Secretsを安全に扱うため）
5. 「Create repository」

#### Step 2: ファイルをアップロード

ブラウザだけで完結します。

1. 作成したリポジトリの「Add file」→「Upload files」
2. このプロジェクトのファイルをドラッグ&ドロップ
   - `.env` は **アップロードしない**（`.gitignore`で除外済）
   - `seen.json`, `weekly_buffer.json` は初回は存在しないのでスキップでOK
3. 「Commit changes」

#### Step 3: Secrets を登録

リポジトリの「Settings」→「Secrets and variables」→「Actions」→「New repository secret」で以下を全て登録:

| Secret名 | 内容 | 取得方法 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude APIキー | [console.anthropic.com](https://console.anthropic.com) でAPIキー発行 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL | Slackアプリ「Incoming Webhooks」を該当チャンネルに追加 |
| `SMTP_HOST` | SMTPサーバ | 例: `smtp.gmail.com` |
| `SMTP_PORT` | SMTPポート | 例: `587` |
| `SMTP_USER` | 送信用メールアドレス | Gmailの場合は自分のアドレス |
| `SMTP_PASS` | SMTPパスワード | Gmailの場合は[アプリパスワード](https://myaccount.google.com/apppasswords)を発行 |
| `MAIL_FROM` | 送信元アドレス | `SMTP_USER` と同じでOK |
| `MAIL_TO` | 送信先（カンマ区切り） | 例: `you@example.com,team@example.com` |

#### Step 4: 動作確認

リポジトリの「Actions」タブ → 「SNS News Monitor」→「Run workflow」で手動実行。

正常終了すれば、以降は自動でスケジュール実行されます。

## 配信例

### 速報モード（Slack）

```
:rotating_light: 重要アップデート速報 (1件)
─────────────────────────────────────
:rotating_light: 【高】Meta Ads APIのv18.0廃止スケジュール変更について
プラットフォーム: Meta
カテゴリ: API
日本対応: ✅利用可能
検知時刻: 2026-04-27T18:00:00

要約
MetaがGraph APIのv18.0を予定より早く2026年6月で廃止すると発表しました。
現在v18.0を使っている広告管理ツールは早めにv19.0以降への移行が必要です。

影響
v18.0を使用中の運用ツールは6月までに更新しないと広告配信や数値取得が止まります。

日本対応の根拠
全世界共通のAPIバージョンポリシー変更のため日本も対象。

出典: Meta Graph API Changelog
```

### 週次モード（メール）

プラットフォーム別にグループ化された一覧形式で送信。重要度「高」は赤、「中」は黄色、「低」はグレーで色分け表示。

## 想定コスト

| 項目 | 月額目安 |
|---|---|
| Claude API（Haiku 4.5想定、月数百件分析） | 〜数百円 |
| GitHub Actions | 無料枠で十分（Private repoでも月2,000分無料） |
| Slack Incoming Webhook | 無料 |
| Gmail SMTP | 無料 |
| **合計** | **月数百円〜千円程度** |

※精度を上げたい場合は `summarizer.py` の `DEFAULT_MODEL` を `claude-sonnet-4-6` 等に変更可能（コスト増）。

## 注意事項

### スクレイピングの規約遵守

- 各サイトのrobots.txt / 利用規約を確認の上、過度なアクセスは行わないこと
- 本ツールは3時間おき・各ソース1リクエスト・2秒間隔で控えめに巡回するよう設計
- サイト側で明示的にスクレイピング禁止が記載されている場合は使用しないこと

### CSSセレクタの保守

- HTMLソースのセレクタはサイト改修で動かなくなる可能性あり
- 取得件数が0件のソースが続く場合は `config.yaml` のセレクタを更新
- ブラウザの開発者ツールで該当要素を確認 → セレクタを取得して `config.yaml` に反映

### 既読管理

- `seen.json` で既読URLを管理（最大10000件、超過時は古いものから削除）
- `weekly_buffer.json` は8日以上前のエントリを自動削除
- GitHub Actions運用時はリポジトリにコミットして永続化（`[skip ci]`付きで自動コミット）

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| Claude APIエラー | `ANTHROPIC_API_KEY` のSecret登録を確認、課金状況を確認 |
| Slackに通知が来ない | Webhook URLが正しいか、対象チャンネルにアプリが追加されているか確認 |
| メールが届かない | Gmailの場合、2段階認証ON＋アプリパスワード使用が必須 |
| ある特定ソースだけ0件 | `config.yaml` のCSSセレクタをブラウザ開発者ツールで再確認 |
| Actions実行失敗 | 「Actions」タブ → 該当run → ログ確認 |

## ライセンス

社内用途想定。再配布は想定していません。
