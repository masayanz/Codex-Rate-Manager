# Codex Rate Manager

Codex Plus の5時間枠と週間枠を Codex App Server から定期取得し、残量・リセット時刻・接続状態をタスクトレイから確認する Windows 常駐アプリです。リセット予定時刻だけで利用可能と判断せず、監視対象の両方の枠を再取得して利用可能を確認した後に通知します。

## 動作環境

- Windows 11
- Python 3.13（ソース実行時）
- Codex CLI（既存のログイン状態を利用）
- PowerShell

アプリは Windows 向けです。WSL2 と `.sh` スクリプトは使用しません。

## セットアップと起動

PowerShellでリポジトリのフォルダーを開き、次を実行します。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m codex_rate_manager
```

開発中は `scripts\run-dev.ps1` からも起動できます。GUIが先に表示され、保存先の初期化とCodex接続はバックグラウンドで行われます。通常はウィンドウを閉じてもタスクトレイに格納され、トレイメニューから開く、今すぐ更新、Codex起動、設定、終了を選べます。

モック確認は実アカウントへ問い合わせず、次で起動します。

```powershell
.\.venv\Scripts\python.exe -m codex_rate_manager --mock
```

モック状態は診断画面から `AVAILABLE`、`LOW`、`LIMITED_5H`、`LIMITED_WEEKLY`、`RESET`、`DISCONNECTED` に切り替えられます。モックの保存先は `%LOCALAPPDATA%\CodexRateManager\mock` です。`--data-dir <フォルダー>` で保存先を指定できます。`--smoke-test SECONDS` は実アプリを指定秒数だけ起動し、診断情報と画面の確認用レポートを保存して終了します。

モックを含め、レート確認のために実際のプロンプトを送信したり、レートを消費するCodex操作を実行したりしません。利用可能判定は、応答で確認できた5時間枠・週間枠に限ります。枠が欠落または解釈できない場合は安全側に倒して接続不明として扱います。

## Codex接続

アプリは `codex app-server` を自身の子プロセスとして起動し、JSON-RPCの `initialize` と `account/rateLimits/read` を使用します。Codex CLIは自動検出されます。設定のCodexタブで実行ファイルを指定することもできます。

Codexのログインはアプリでは行いません。先に通常のCodex CLIでログインしてください。ログイン用トークン、Cookie、認証情報をアプリの設定やログへ保存・出力しません。App Serverの仕様は [Codex App Server公式ドキュメント](https://learn.chatgpt.com/docs/app-server) を参照してください。

切断時はアプリを終了せず、30秒、60秒、120秒のバックオフで再接続します。診断画面ではCLI、接続状態、App Server PID、version、最終取得時刻、5h/weekly windowを確認できます。

## 保存先と秘密情報

既定の保存先は `%LOCALAPPDATA%\CodexRateManager` です。モックはその下の `mock` に分離されます。

- `config.json`: 通常監視間隔、通知ON/OFF、Codexパスなど
- `webhook.bin`: Windows DPAPIで保護したDiscord Webhook URL
- `rate_history.db`: レート、イベント、通知履歴
- `logs\app.log`: アプリログ。Webhook URLなどの秘密情報はマスクされます

Webhook URLを直接JSONへ平文保存せず、現在のWindowsユーザーに結び付いたDPAPIで保護します。ユーザーやWindowsアカウントを変更すると復号できなくなるため、設定画面で再登録してください。

## Discord通知

Discordで通知するチャンネルを開き、次の順にWebhookを作成します。

`サーバー設定` → `連携サービス` → `ウェブフック` → `新しいウェブフック`

通知先チャンネルを選択してURLをコピーし、アプリの設定のDiscordタブへ貼り付けます。URL全体は画面やログに表示せず、入力欄はマスク表示です。Discordの公式手順は [Discord公式サポート（日本語）](https://support.discord.com/hc/ja/articles/360045093012) を参照してください。

Discord通知を有効にした後、設定画面の「Discord通知テスト」で送信を確認できます。Windows通知とDiscord通知、さらに低残量・上限到達・リセット完了は個別にON/OFFできます。Discordの失敗はレート監視やWindows通知を停止しません。Webhookの同一イベントはイベント種別とreset epochで一度だけ送信します。

## 自動起動と通知

設定の一般タブで「Windowsログイン時に起動」と「起動時にウィンドウを表示」を設定できます。自動起動は管理者権限を使わず、現在のユーザーのスタートアップへ登録します。EXEを移動した場合は、設定を一度OFFにして保存し、移動先から再度ONにして登録を更新してください。

監視間隔の既定値は5分です。リセット10分前は通常より短い間隔になり、予定時刻を過ぎたときは再取得確認後も短時間の連続問い合わせを避けます。カウントダウン表示はGUIで更新しますが、1秒ごとにCodexへ問い合わせません。

## EXEビルド

依存関係、テスト、PyInstallerの順に実行し、フォルダー形式（onedir）のEXEを作成します。

```powershell
.\scripts\build.ps1
```

生成先は `dist\CodexRateManager\CodexRateManager.exe` です。フォルダー全体を同じ場所に置いて起動してください。ビルド後にEXEを移動した場合は自動起動設定を再保存してください。

## トラブルシューティング

「Codex情報を取得できません」と表示される場合は、Codex CLIがPATHにあるか、設定のCodex実行ファイルが正しいか、通常のCLIでログイン済みかを確認してください。アプリは外部の `codex login` を呼び出さず、既存のCodex認証を利用します。

DPAPIエラーが出た場合は、同じWindowsユーザーで起動しているかを確認し、Webhook URLを設定画面から再登録してください。Webhook URLをログやスクリーンショットで共有しないでください。

Windows通知が見えない場合は、Windowsの通知設定、集中モード、アプリの通知許可を確認してください。DiscordのHTTP 429はDiscordが返す待機時間に従って再送します。400系の場合はWebhookのチャンネル、URL、権限を設定画面のテスト送信で確認してください。

認証やネットワークが一時的に失敗しても、アプリは終了せず自動再接続を続けます。診断画面と `%LOCALAPPDATA%\CodexRateManager\logs\app.log` のエラー種別を確認してください。

## 検証について

実施済みのテストと実機確認、未確認条件は [検証記録](VERIFICATION.md) を参照してください。実Discordへの着信確認には、ご自身のWebhook URLを設定し、テスト送信してください。
