# Codex Rate Manager

Codex Plus の5時間枠と週間枠を Codex App Server から定期取得し、残量・リセット時刻・接続状態をタスクトレイから確認する Windows 常駐アプリです。リセット予定時刻だけで利用可能と判断せず、各枠の前回取得残量と今回残量を独立して比較し、1.0ポイント以上増えた枠を回復として通知します。0%からの復帰だけでなく途中補充・強制リセットも対象です。もう一方の枠が減少中・0%でも通知し、両枠が増えれば各1件送ります。起動後の初回取得は基準値の保存だけを行います。

## 動作環境

スクリーンショット付きの操作説明は [利用者マニュアル（HTML）](docs/manual/index.html) を参照してください。ブラウザーで開いて閲覧できます。配布時は `docs/manual` フォルダー全体を渡してください。

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

復帰イベントの実ワーカー経路は、空の検証用フォルダーで検証できます。HTTP 204とWindows通知を差し替え、結果を `recovery-verification.json`、履歴をDB、遷移を `logs/app.log` に保存します。

```powershell
.\.venv\Scripts\python.exe -m codex_rate_manager --mock --verify-recovery --data-dir test-artifacts/recovery-mock
```

保存済みWebhookをメモリへ読み込み、実際のDiscordへテストを1回・復帰通知を2回、Windowsへ復帰通知を2回送る場合は次を使用します。通知にはMock検証と明記します。Webhook URLは出力・保存しません。

```powershell
.\dist\CodexRateManager.exe --mock --verify-recovery --live-notifications --data-dir test-artifacts/recovery-live
```

モックを含め、レート確認のために実際のプロンプトを送信したり、レートを消費するCodex操作を実行したりしません。利用可能判定は、応答で確認できた5時間枠・週間枠に限ります。枠が欠落または解釈できない場合は安全側に倒して接続不明として扱います。

## パネルとタスクトレイ

下部の「スクショ」、メイン画面の `Ctrl+Shift+C`、トレイメニューの「画面をコピー」で、現在のメイン画面内部を画像としてクリップボードへコピーできます。ペイントやDiscord、ChatGPTなどへ `Ctrl+V` で貼り付けてください。デスクトップ、他アプリ、別ウィンドウの設定・診断画面、タイトルバーは含めません。PNGはメモリ上で生成し、ファイルには保存しません。

トレイ格納中・最小化中は一時表示して描画を待ち、結果を約1.8秒表示して元の状態へ戻します。連続実行中の追加操作は無視します。失敗時は画面へ通知し、詳細（例外の種類・発生箇所）をログへ記録します。

標準520×720（クライアント領域）のコンパクトなNeon Futureパネルで、5時間枠・週間枠を28分割リングとセグメントバーに表示します。画面が小さい場合は内容のみスクロールでき、下部の操作ボタンは固定表示します。

トレイの外周は5時間、内周は週間の残量です。設定の「外観」で上下バー（上5時間・下週間）へ変更できます。70%以上はシアン、40%以上は緑、20%以上は黄、20%未満は赤系、0%は赤です。接続確認中・切断時は灰色となり、古い値には「最終取得値」と表示します。

マウスオーバーで両枠の残量とリセット日時を確認できます。右クリックメニューにも現在値を表示し、履歴・設定・診断・通知テストを開けます。事前通知の初期値は10分前です。保存済み設定は維持します。

## タスクトレイの表示・非表示

設定 → 一般の「タスクトレイアイコンを表示する」で即時に切り替えます。この項目は切替時点で保存されるため、「閉じる」でも元には戻りません。初期値はONで、従来の `tray_enabled` 設定をそのまま引き継ぎます。非表示中もウィンドウを開いている間は監視・通知を継続します。

トレイが非表示の状態で×を押すと、「アプリを終了」「タスクトレイを表示して閉じる」「キャンセル」を選べます。「表示して閉じる」は設定保存に成功してからトレイを表示し、ウィンドウを格納します。保存に失敗した場合はウィンドウを残します。

自動起動ON・起動時ウィンドウ非表示の場合はトレイONを必須とします。自動起動OFFで両方非表示を指定した場合は、起動時ウィンドウ表示をONに補正します。実行中にトレイを隠す際も、必要なら先にメインウィンドウを再表示します。

「Windowsの通知領域設定を開く」は、Microsoftが公開している [`ms-settings:taskbar`](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings) でタスクバー設定を開きます。Windows側で通知領域の表示位置を調整してください。アプリから常時表示側への強制固定は行いません。開けない場合は、タスクバーを右クリックして「タスクバーの設定」を開いてください。

表示設定の変更が保存されると、イベント履歴へ `TRAY_ICON_SHOWN` / `TRAY_ICON_HIDDEN` を記録します。

## スキンと外観

設定 → 外観で **Neon Future / Graphite / Minimal Dark** を切り替えられます。選択直後に本体とトレイへプレビューし、「保存」で確定します。保存せず閉じると保存済みの外観へ戻ります。発光と更新時の約300msのメーターアニメーションは個別に無効化できます。

標準3スキンと専用アイコンはEXEに内包しています。追加スキンは `%LOCALAPPDATA%\CodexRateManager\skins\my_skin\` に `skin.json` と `style.qss` を置き、アプリを再起動すると検出します。開発用の保存先指定やモックでは、その保存先の `skins` を使います。

スキン作成時は `assets/skins/neon_future` をコピーし、JSONの `id` と `name` を変更してください。IDは小文字英数字と `_` / `-` で指定し、標準スキンと同じIDは使えません。QSSは `{{colors.accent}}` や `{{layout.card_radius}}` のトークンを参照できます。色・角丸・余白・発光強度はJSONにまとめています。不正なスキンは読み飛ばし、保存済みスキンが見つからない場合はNeon Futureへ戻ります。

## ウィンドウ位置とアイコン

初回はプライマリモニターの右側中央へ表示します。移動・サイズ変更後と終了時に外枠の位置・サイズ・モニターを保存します。最小化・最大化中の座標は保存せず、最後の通常位置を使います。

復元・トレイから再表示・画面構成/DPI変更時は、タスクバーを除いた利用可能領域へタイトルバーごと補正します。保存したモニターが見つからなければプライマリへ戻します。以前の設定、履歴、Webhookは維持します。

EXE、ウィンドウ、タスクバー、Alt+Tab用には同じ複数サイズICOを設定し、固定AppUserModelID `CodexRateManager.Desktop` を使用します。トレイはスキン配色の動的メーターを継続し、初期化中・描画失敗時は専用ICOを表示します。

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

Discord通知を有効にした後、設定画面の「Discord通知テスト」で送信を確認できます。Windows通知とDiscord通知、さらに低残量・上限到達・リセット完了は個別にON/OFFできます。Discordの失敗はレート監視やWindows通知を停止しません。回復イベントはリセット予定時刻と無関係な固有キーで重複防止します。同じ予定時刻内の再補充や連続増加も各イベントとして通知します。

## 自動起動と通知

設定の一般タブで「Windowsログイン時に起動」と「起動時にウィンドウを表示」を設定できます。自動起動は管理者権限を使わず、現在のユーザーのスタートアップへ登録します。EXEを移動した場合は、設定を一度OFFにして保存し、移動先から再度ONにして登録を更新してください。

監視間隔の既定値は5分です。リセット10分前は通常より短い間隔になり、予定時刻を過ぎたときは再取得確認後も短時間の連続問い合わせを避けます。カウントダウン表示はGUIで更新しますが、1秒ごとにCodexへ問い合わせません。

## EXEビルド

依存関係、テスト、PyInstallerの順に実行し、1ファイル形式（onefile / windowed）のEXEを作成します。

```powershell
.\scripts\build.ps1
```

生成先は `dist\CodexRateManager.exe` です。このEXEだけを配布・移動して起動できます。Codex CLIとログイン済み環境は必要です。ビルド後にEXEを移動した場合は自動起動設定を再保存してください。既存の配布物は `.cache/build-preserved` へ退避します。ビルド前に旧版をトレイメニューの「終了」から終了してください。

## トラブルシューティング

Discordのテストだけ届く場合は、設定の「通知を有効にする」「Discord通知を有効にする」「各レート枠の復帰」をすべてONにして保存してください。テストは接続確認のため、自動通知設定OFFでも送信します。設定画面に復帰通知のON/OFFを表示します。

上限・予定時刻到達・確認済み復帰は `RATE_5H_LIMIT_REACHED` / `RATE_5H_RESET_DUE` / `RATE_5H_RECOVERED`（週間は `RATE_WEEKLY_…`）として記録します。5時間・週間の復帰イベントはそれぞれ通知し、同時復帰では各1回通知します。Codex全体の利用可能状態を通知条件には使いません。本文には両枠の現在残量、対象枠の前回・今回・増加量、取得確認時刻とリセット予定日時を表示します。通知履歴テーブルは既存の `notifications` を使用し、`result=1` がSUCCESSです。

ログの `NOTIFICATION_ROUTING` で設定判定、`NOTIFICATION_SKIPPED` で抑止理由、`discord_send_attempted` / `discord_send_result` で試行とHTTP結果を確認できます。イベントキーには制限を最初に観測したリセット時刻を使用します。キュー満杯や送信失敗では通知のclaimを解放し、同一イベントの再処理を妨げないようにします。

「Codex情報を取得できません」と表示される場合は、Codex CLIがPATHにあるか、設定のCodex実行ファイルが正しいか、通常のCLIでログイン済みかを確認してください。アプリは外部の `codex login` を呼び出さず、既存のCodex認証を利用します。

DPAPIエラーが出た場合は、同じWindowsユーザーで起動しているかを確認し、Webhook URLを設定画面から再登録してください。Webhook URLをログやスクリーンショットで共有しないでください。

Windows通知が見えない場合は、Windowsの通知設定、集中モード、アプリの通知許可を確認してください。DiscordのHTTP 429はDiscordが返す待機時間に従って再送します。400系の場合はWebhookのチャンネル、URL、権限を設定画面のテスト送信で確認してください。

認証やネットワークが一時的に失敗しても、アプリは終了せず自動再接続を続けます。診断画面と `%LOCALAPPDATA%\CodexRateManager\logs\app.log` のエラー種別を確認してください。

## 検証について

実施済みのテストと実機確認、未確認条件は [検証記録](VERIFICATION.md) を参照してください。実Discordへの着信確認には、ご自身のWebhook URLを設定し、テスト送信してください。

Discord通知のON/OFFは設定画面で切り替えた時点で保存します。その他の通知設定は「保存」で反映します。config.jsonを直接編集する場合は、アプリを終了してから編集し、再起動してください。

## 残量増加による回復検知

回復しきい値は1.0ポイントです（例：42%→43%は通知、42%→42.1%は通知なし）。比較基準は前回の有効な取得値で、1ポイント未満の増加を累積しません。減少・同値でも基準を更新し、通知OFF中も監視を続けます。起動・再起動後やアカウント変更後の初回値から回復を推測しません。取得失敗・欠落した枠の基準値は更新せず、取得できた枠は独立に比較します。片方が欠落している通知では、その枠を「未取得」と表示し、UI全体を使用可能とは判定しません。

`resetsAt` はUI表示・事前通知・再取得スケジュールに使用し、回復の条件にはしません。`RATE_COMPARE` に前回・今回・差分を記録します。「履歴」→「回復履歴」で前回残量・今回残量・増加・両枠の残量を確認できます。DBの `recoveries` テーブルを追加し、既存の履歴・設定は維持します。通知設定キーは既存の `discord_enabled` と `discord_reset` を継続します。Discord ON/OFFの保存中は再操作と全体保存を止め、完了後に保存結果を表示します。

必須7ケースと強制リセット・途中補充を、実監視ワーカー・DB・共通通知経路で検証できます。Discord HTTPとWindows通知登録だけをMockへ差し替えるため外部送信はありません。空の保存先を指定してください。

```powershell
.\dist\CodexRateManager.exe --mock --verify-increases --data-dir test-artifacts/increase-check
```

結果は `increase-verification.json`、比較・通知ログは `logs/app.log`、履歴は `rate_history.db` に保存します。実サービスの着信やWindows上の表示を確認する検証ではありません。
