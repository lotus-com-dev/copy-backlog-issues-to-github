# copy-backlog-issues-to-github

Backlogプロジェクトの課題をGitHubリポジトリのIssueにコピーするツールです.

- 課題の本文とコメントを, IssueとIssueコメントとしてコピーします.
- Backlog記法で書かれた本文は, Markdown記法に変換を試みます.
- 画像と動画の添付ファイルは, Issueに直接添付します.
- それ以外の添付ファイルは, リポジトリにコミットしたファイルへのリンクにします.
- Backlogで状態が「完了」の課題は, Issueをcloseします.
- 課題の状態と種別を, Issueのラベルとしてコピーします.
- 実行を中断した場合は, 再実行すれば続きから処理します.

コピー先のGitHubリポジトリは, 課題管理とファイル保管の専用リポジトリとして使う想定です.

## 必要なもの

- Python 3.12以上と [uv](https://docs.astral.sh/uv/)
- [GitHub CLI](https://cli.github.com/) (`gh`). `gh auth login` で認証を済ませてください.
- BacklogのAPIキー. Backlogの「個人設定」→「API」で発行できます.
- コピー先のGitHubリポジトリ(空のリポジトリを推奨). ローカルにcloneしてください.

## インストール

```bash
git clone https://github.com/lotus-com-dev/copy-backlog-issues-to-github.git
cd copy-backlog-issues-to-github
uv tool install .
```

以降は `copy-backlog-issues-to-github` コマンドとして実行できます.

## 使い方

例として, Backlogのドメインを `example.backlog.jp`, プロジェクトキーを `PROJ`, コピー先を `your-org/your-repo`(デフォルトブランチ `main`)とします.

コマンドは, コピー先リポジトリのcloneのルートディレクトリで実行してください.
添付ファイルへのリンクは, このディレクトリをリポジトリのルートとして生成します.

```bash
cd path/to/your-repo
```

### 1. Backlogから課題を取得する

```bash
export BACKLOG_API_KEY=xxxxxxxx

copy-backlog-issues-to-github copy-from-backlog example.backlog.jp PROJ
```

ドメインは `example.backlog.jp` などの場合もあります. BacklogにアクセスするURLのホスト名を指定してください.
APIキーはコマンド履歴に残らないよう, 環境変数 `BACKLOG_API_KEY` で渡します.

課題の情報, コメント, 添付ファイルが, 次の構成で保存されます.

```
PROJ/
├── project.json                # プロジェクト情報
├── issues.json                 # 課題一覧
└── PROJ-1/
    ├── info.json               # 課題情報
    ├── comments.json           # コメント一覧
    └── 12345/                  # 添付ファイルID
        └── 仕様書.pdf
```

取得済みのファイルはスキップします. 中断した場合は, 同じコマンドを再実行してください.
取り直したい場合は, 該当するファイルを削除してから実行してください.

### 2. Issueに直接添付できないファイルをコミットする

画像と動画で10MB以下のファイルは, Issueに直接添付します.
対象の拡張子は png, jpg, jpeg, gif, webp, svg, mp4, mov, webm です.
それ以外のファイルはリポジトリへのリンクにするため, 事前にコミットしてpushしておきます.

```bash
copy-backlog-issues-to-github list-unattachable-files --null PROJ | xargs -0 git add
git commit -m "Backlogの添付ファイルを追加"
git push
```

> [!IMPORTANT]
> 日本語のファイル名を含む場合は, ファイル名をNFC形式でコミットしてください.
> Issueからのリンクは, NFC形式のファイル名で生成します.
> macOSのgitは, 既定の設定(`core.precomposeunicode=true`)でNFC形式にしてコミットするようになっています.
> 設定を変更している場合は, 次のコマンドで既定に戻してください.
>
> ```bash
> git config core.precomposeunicode true
> ```

> [!WARNING]
> GitHubには100MBを超えるファイルをそのままpushできません.
> 該当するファイルは次のコマンドで確認できます.
> Git LFSを使うなど, 個別に対応してください.
>
> ```bash
> copy-backlog-issues-to-github list-oversized-files PROJ
> ```

### 3. GitHubにIssueを作成する

```bash
copy-backlog-issues-to-github copy-to-github example.backlog.jp PROJ your-org/your-repo main
```

本文中の課題リンク(`[[PROJ-1]]`)は, 指定したドメインのBacklogの課題へのリンクに変換します.

処理の進み具合は `PROJ/github-issues.json` に記録します.
再実行すると, 作成済みのIssueと追加済みのコメントはスキップします.
このファイルは削除しないでください.

> [!NOTE]
> 課題の状態は, 手順1で取得した `PROJ/PROJ-1/info.json` を参照します.
> 取得後にBacklogで完了にした課題は, `info.json` を削除して手順1を再実行すると, 次回の手順3でIssueがcloseされます.
> Backlogで完了を取り消しても, closeしたIssueは再openしません.

> [!NOTE]
> 課題の状態と種別は, `状態: 処理中`, `種別: バグ` のようなラベルとしてIssueに付けます.
> ラベルはBacklogと同じ色で自動的に作成します.
> ラベルを付けるのはIssueの作成時(作成済みのIssueは次回の実行時)の1回だけです.
> その後にBacklogで状態や種別を変えても, ラベルは追従しません.

> [!NOTE]
> GitHubのレート制限を超えないよう, Issueの作成, コメントの追加, close, ラベルの作成と付与は7.2秒に1件のペースで行います.
> 1時間あたり約500件です. 課題とコメントが多いプロジェクトでは, 数時間かかることがあります.

## コマンド一覧

| コマンド | 内容 |
|---|---|
| `copy-from-backlog <backlogDomain> <projectKey>` | Backlogから課題, コメント, 添付ファイルを取得する |
| `copy-to-github <backlogDomain> <projectKey> <repoUrl> <repoBranch>` | 取得した課題からGitHubにIssueを作成する |
| `list-unattachable-files <projectKey>` | Issueに直接添付できない添付ファイルのパスを一覧する |
| `list-oversized-files <projectKey>` | 上記のうち, 100MBを超えるファイルのパスを一覧する |

主なオプションは次のとおりです.

| オプション | 対象 | 内容 |
|---|---|---|
| `--working-dir <dir>` | 全コマンド | 作業ディレクトリ. 省略時はカレントディレクトリ |
| `--issue-key <issueKey>` | 全コマンド | 対象の課題を絞り込む. 複数指定可 |
| `--no-download` | `copy-from-backlog` | 添付ファイルをダウンロードしない |
| `--null` | `list-*` | 区切り文字をNUL文字にする. `xargs -0` と併用する |

詳しい仕様は [spec.md](spec.md) を参照してください.

## コピーされる内容

- Issueのタイトルには, 課題の件名を使います.
- 課題の状態と種別を, `状態: <状態名>`, `種別: <種別名>` のラベルとして付けます.
- 状態が「完了」の課題は, コメントをすべて追加した後にIssueをcloseします.
  - closeの日時は, コマンドを実行した日時になります.
- Issueとコメントの末尾には, 元の課題とコメントの情報を引用の形で記載します.
  - 課題キー, 課題ID, コメントID, 作成者, 作成日時
- Backlogから削除された添付ファイルは, `ファイル名 (削除済み)` と表示します.
- 自動リンクを防ぐため, `@user` や `#123` の `@` と `#` の直後にゼロ幅スペースを挿入します.
  - GitHub上の無関係なユーザーへのメンションや, 無関係なIssueへの参照になるのを防ぐためです.
- 次の情報はコピーしません.
  - 担当者, 優先度, 期限日などの属性(状態と種別以外)

## 開発

```bash
uv sync
uv run pytest
```

## ライセンス

[Apache License 2.0](LICENSE)

Copyright 2026 Lotus & Company inc.
