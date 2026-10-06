from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from .backlog_markdown import convert_backlog_notation_to_markdown
from .common import (
    ProgressPrinter,
    atomic_write_json,
    attachment_relative_path,
    filter_target_issues,
    read_json,
)

_ATTACHABLE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "mp4",
    "mov",
    "webm",
}
_ATTACHABLE_MAX_SIZE = 10 * 1024 * 1024
_GITHUB_MAX_FILE_SIZE = 100 * 1024 * 1024

_ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)")

# GitHubのメンション(@user)として解釈される@. メールアドレスやURL中の@は対象外とする.
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_/])@(?=[A-Za-z0-9])")
# GitHubのIssue参照(#123)として解釈される#. URLのフラグメントやHTMLの文字参照中の#は対象外とする.
_ISSUE_REFERENCE_RE = re.compile(r"(?<![A-Za-z0-9_/&])#(?=[0-9])")
_ZERO_WIDTH_SPACE = "\u200b"
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+).+?(?<!`)\1(?!`)")

_LINK_TEXT_SPECIAL_RE = re.compile(r"([\\`*_\[\]<>~|])")

# GitHubの二次レート制限ではコンテンツ作成系のリクエスト (issue作成・コメント追加) は
# 毎分80件, 毎時500件までとされているため, 毎時500件を超えない間隔を空ける.
_MIN_REQUEST_INTERVAL_SECONDS = 3600 / 500

# gh コマンド経由では Retry-After 等のレスポンスヘッダーが取得できないため,
# stderr のメッセージからレート制限によるエラーかどうかを判定し,
# 指数バックオフで再試行する.
_RETRYABLE_ERROR_PATTERNS = [
    re.compile(r"submitted too quickly", re.IGNORECASE),
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"RATE_LIMITED"),
    re.compile(r"abuse detection", re.IGNORECASE),
]
# リトライしても成功しないエラー. 即座に失敗させる.
_NON_RETRYABLE_ERROR_PATTERNS = [
    re.compile(r"is too long", re.IGNORECASE),
    re.compile(r"validation failed", re.IGNORECASE),
    re.compile(r"could not resolve to", re.IGNORECASE),
    re.compile(r"bad credentials", re.IGNORECASE),
    re.compile(r"gh auth login", re.IGNORECASE),
    re.compile(r"unknown (shorthand )?flag", re.IGNORECASE),
    re.compile(r"HTTP (400|401|404|422)\b"),
]
# GitHubのIssue本文, コメントの最大文字数
_MAX_BODY_LENGTH = 65536
_MAX_RATE_LIMIT_RETRIES = 10
# レート制限とは断定できない一時的なエラー (GitHub側のGraphQLエラー等,
# リトライすれば成功することがある) も,念のため少数回だけ再試行する.
_MAX_GENERIC_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 60.0
_RETRY_MAX_DELAY_SECONDS = 3600.0
# リトライ前の作成済み確認で調べる直近のIssue数.
# リポジトリはこのツール専用の想定のため, 直近の数件に含まれるはず.
_EXISTENCE_CHECK_ISSUE_LIMIT = 30
# Backlogの状態「完了」のID. 1〜4は予約されており, 4が完了.
_BACKLOG_STATUS_ID_COMPLETED = 4
# 状態と種別で同名がありうる(GitHubのラベル名は大文字小文字を区別しない)ため接頭辞で区別する
_STATUS_LABEL_PREFIX = "状態: "
_ISSUE_TYPE_LABEL_PREFIX = "種別: "
_LABEL_DESCRIPTIONS = {
    _STATUS_LABEL_PREFIX: "Backlogの課題の状態",
    _ISSUE_TYPE_LABEL_PREFIX: "Backlogの課題の種別",
}
_MAX_LABEL_NAME_LENGTH = 50
_LABEL_COLOR_RE = re.compile(r"#?([0-9A-Fa-f]{6})")
_LABEL_LIST_LIMIT = 1000

_last_request_monotonic: float | None = None


class CopyToGithubError(Exception):
    pass


class _Label(NamedTuple):
    name: str
    color: str
    description: str


def copy_to_github(
    *,
    working_dir: str,
    project_key: str,
    repo_url: str,
    repo_branch: str,
    issue_keys: list[str] | None,
    domain: str | None,
) -> None:
    project_dir = Path(working_dir) / project_key
    project = read_json(project_dir / "project.json")
    issues = read_json(project_dir / "issues.json")
    text_formatting_rule = project["textFormattingRule"]

    target_issues = filter_target_issues(issues, issue_keys)

    record_path = project_dir / "github-issues.json"
    record = _load_record(record_path)

    total_comments = 0
    for issue in target_issues:
        issue_dir = project_dir / issue["issueKey"]
        total_comments += len(read_json(issue_dir / "comments.json"))

    # Issue作成時の--labelは存在しないラベルを指定するとエラーになるため, 先に作成しておく
    labels_to_ensure: dict[str, _Label] = {}
    for issue in target_issues:
        if "labels" in record.get(issue["issueKey"], {}):
            continue
        info = read_json(project_dir / issue["issueKey"] / "info.json")
        for label in _issue_labels(info):
            labels_to_ensure.setdefault(label.name, label)
    if labels_to_ensure:
        _ensure_labels(repo_url, list(labels_to_ensure.values()))

    progress = ProgressPrinter(total_comments)
    for issue in target_issues:
        _copy_issue(
            project_dir=project_dir,
            issue_key=issue["issueKey"],
            repo_url=repo_url,
            repo_branch=repo_branch,
            text_formatting_rule=text_formatting_rule,
            domain=domain,
            record=record,
            record_path=record_path,
            progress=progress,
        )


def list_unattachable_files(
    *,
    working_dir: str,
    project_key: str,
    issue_keys: list[str] | None,
    null: bool,
) -> None:
    _print_matching_paths(
        working_dir=working_dir,
        project_key=project_key,
        issue_keys=issue_keys,
        null=null,
        predicate=lambda name, size: not _is_attachable(name, size),
    )


def list_oversized_files(
    *,
    working_dir: str,
    project_key: str,
    issue_keys: list[str] | None,
    null: bool,
) -> None:
    _print_matching_paths(
        working_dir=working_dir,
        project_key=project_key,
        issue_keys=issue_keys,
        null=null,
        predicate=lambda name, size: (
            not _is_attachable(name, size) and _is_oversized(size)
        ),
    )


def _print_matching_paths(
    *,
    working_dir: str,
    project_key: str,
    issue_keys: list[str] | None,
    null: bool,
    predicate: Callable[[str, int | None], bool],
) -> None:
    project_dir = Path(working_dir) / project_key
    issues = read_json(project_dir / "issues.json")
    target_issues = filter_target_issues(issues, issue_keys)

    separator = "\0" if null else "\n"
    printed_paths: set[Path] = set()
    for issue in target_issues:
        issue_dir = project_dir / issue["issueKey"]
        for path in _matching_paths_for_issue(issue_dir, predicate):
            if path in printed_paths:
                continue
            printed_paths.add(path)
            sys.stdout.write(f"{path}{separator}")


def _matching_paths_for_issue(
    issue_dir: Path, predicate: Callable[[str, int | None], bool]
) -> list[Path]:
    paths: list[Path] = []
    for attachments in _iter_issue_attachment_groups(issue_dir):
        for attachment in attachments:
            if attachment.get("deleted"):
                continue
            if not predicate(attachment["name"], attachment.get("size")):
                continue

            local_path = issue_dir / attachment_relative_path(attachment)
            if local_path.exists():
                paths.append(local_path)
            else:
                print(
                    f"警告: 添付ファイル '{local_path}' が見つからないため一覧から除外します",
                    file=sys.stderr,
                )
    return paths


def _iter_issue_attachment_groups(issue_dir: Path) -> Iterator[list[dict[str, Any]]]:
    info = read_json(issue_dir / "info.json")
    comments = read_json(issue_dir / "comments.json")
    attachment_by_id = {a["id"]: a for a in info.get("attachments") or []}

    yield _issue_level_attachments(attachment_by_id, comments)
    for comment in sorted(comments, key=lambda c: c["created"]):
        yield _comment_attachments(comment, attachment_by_id)


def _copy_issue(
    *,
    project_dir: Path,
    issue_key: str,
    repo_url: str,
    repo_branch: str,
    text_formatting_rule: str,
    domain: str | None,
    record: dict[str, Any],
    record_path: Path,
    progress: ProgressPrinter,
) -> None:
    issue_dir = project_dir / issue_key
    info = read_json(issue_dir / "info.json")
    comments = read_json(issue_dir / "comments.json")
    attachment_by_id = {a["id"]: a for a in info.get("attachments") or []}
    issue_level_attachments = _issue_level_attachments(attachment_by_id, comments)

    issue_record = record.setdefault(issue_key, {})
    issue_record.setdefault("comments", [])

    label_names = (
        None
        if "labels" in issue_record
        else [label.name for label in _issue_labels(info)]
    )

    if "number" in issue_record:
        print(f"スキップ: {issue_key} のIssueは作成済み(#{issue_record['number']})です")
    else:
        number = None
        if issue_record.get("pendingIssue"):
            # 前回の実行はIssue作成中に中断しており, 実際には作成済みの可能性がある
            url = _find_created_issue_url(
                repo_url, _issue_marker(issue_key, info["id"])
            )
            if url is not None:
                number = _parse_issue_number(url)
                print(
                    f"確認: {issue_key} のIssueは前回の実行で作成済み(#{number})でした"
                )

        if number is None:
            issue_record["pendingIssue"] = True
            atomic_write_json(record_path, record)
            number = _create_issue(
                issue_dir=issue_dir,
                project_dir=project_dir,
                info=info,
                repo_url=repo_url,
                repo_branch=repo_branch,
                text_formatting_rule=text_formatting_rule,
                domain=domain,
                attachments=issue_level_attachments,
                label_names=label_names or [],
            )
            print(f"作成: {issue_key} -> Issue #{number}")
            issue_record["labels"] = label_names

        issue_record["number"] = number
        issue_record.pop("pendingIssue", None)
        atomic_write_json(record_path, record)

    issue_number = issue_record["number"]
    if label_names is not None and "labels" not in issue_record:
        # ラベルが付与されていないIssueにラベルを付与する.
        # ラベル付与に対応する前に作成したIssueなどが該当する.
        # 付与済みのラベルを再度付与しても成功するため, 事前確認は行わない.
        if label_names:
            _run_gh(
                [
                    "gh",
                    "issue",
                    "edit",
                    str(issue_number),
                    "-R",
                    repo_url,
                    *_label_args("--add-label", label_names),
                ],
                error_message="Issueへのラベル付与に失敗しました",
            )
            print(
                f"ラベル: {issue_key} の Issue #{issue_number} に"
                f" {', '.join(label_names)} を付与しました"
            )
        issue_record["labels"] = label_names
        atomic_write_json(record_path, record)

    posted_comment_ids = set(issue_record["comments"])
    for comment in sorted(comments, key=lambda c: c["created"]):
        comment_id = comment["id"]
        if comment_id in posted_comment_ids:
            progress.update()
            continue

        comment_body = _build_comment_body(
            issue_dir=issue_dir,
            project_dir=project_dir,
            issue_key=issue_key,
            comment=comment,
            repo_branch=repo_branch,
            text_formatting_rule=text_formatting_rule,
            domain=domain,
            attachment_by_id=attachment_by_id,
        )
        marker = _comment_marker(issue_key, comment_id)
        if comment_body is None:
            print(
                f"スキップ: {issue_key} のコメント {comment_id} は内容が空のため追加しません"
            )
        elif issue_record.get("pendingComment") == comment_id and _is_comment_posted(
            repo_url, issue_number, marker
        ):
            # 前回の実行はこのコメントの追加中に中断していたが, 実際には追加済みだった
            print(
                f"確認: {issue_key} のコメント {comment_id} は前回の実行で追加済みでした"
            )
        else:
            issue_record["pendingComment"] = comment_id
            atomic_write_json(record_path, record)
            body, attach_paths = comment_body
            _post_comment(
                repo_url=repo_url,
                issue_number=issue_number,
                body=body,
                attach_paths=attach_paths,
                marker=marker,
            )
            print(f"追加: {issue_key} にコメント {comment_id} を追加しました")

        issue_record["comments"].append(comment_id)
        issue_record.pop("pendingComment", None)
        atomic_write_json(record_path, record)
        progress.update()

    is_completed = info["status"]["id"] == _BACKLOG_STATUS_ID_COMPLETED
    if is_completed and not issue_record.get("closed"):
        # 閉じ済みのIssueを閉じても成功するため, 閉じた直後に中断して再実行しても問題ない
        _run_gh(
            [
                "gh",
                "issue",
                "close",
                str(issue_number),
                "-R",
                repo_url,
                "--reason",
                "completed",
            ],
            error_message="Issueのクローズに失敗しました",
        )
        print(f"クローズ: {issue_key} は完了のため Issue #{issue_number} を閉じました")
        issue_record["closed"] = True
        atomic_write_json(record_path, record)


def _create_issue(
    *,
    issue_dir: Path,
    project_dir: Path,
    info: dict[str, Any],
    repo_url: str,
    repo_branch: str,
    text_formatting_rule: str,
    domain: str | None,
    attachments: list[dict[str, Any]],
    label_names: list[str],
) -> int:
    issue_key = info["issueKey"]
    description = info.get("description") or ""
    converted = _convert_text(description, text_formatting_rule, domain)

    attach_paths, link_lines = _resolve_attachments(
        attachments, issue_dir, project_dir.name, issue_key, repo_branch
    )

    marker = _issue_marker(issue_key, info["id"])
    footer_lines = [
        marker,
        f"作成者: {info['createdUser']['name']}",
        f"作成日時: {info['created']}",
    ]
    body = _render_body(converted, link_lines, footer_lines)

    output = _run_gh(
        [
            "gh",
            "issue",
            "create",
            "-R",
            repo_url,
            "--title",
            info["summary"],
            *_label_args("--label", label_names),
        ],
        body=body,
        attach_paths=attach_paths,
        error_message="Issue作成に失敗しました",
        find_existing=lambda: _find_created_issue_url(repo_url, marker),
    )
    return _parse_issue_number(output)


def _build_comment_body(
    *,
    issue_dir: Path,
    project_dir: Path,
    issue_key: str,
    comment: dict[str, Any],
    repo_branch: str,
    text_formatting_rule: str,
    domain: str | None,
    attachment_by_id: dict[int, dict[str, Any]],
) -> tuple[str, list[str]] | None:
    """コメントの本文と--attachするファイルパスを返す. 投稿する内容が無ければ None を返す."""
    content = comment.get("content") or ""
    converted = (
        _convert_text(content, text_formatting_rule, domain) if content.strip() else ""
    )

    comment_attachments = _comment_attachments(comment, attachment_by_id)
    attach_paths, link_lines = _resolve_attachments(
        comment_attachments, issue_dir, project_dir.name, issue_key, repo_branch
    )

    if not converted.strip() and not attach_paths and not link_lines:
        return None

    footer_lines = [
        _comment_marker(issue_key, comment["id"]),
        f"投稿者: {comment['createdUser']['name']}",
        f"投稿日時: {comment['created']}",
    ]
    return _render_body(converted, link_lines, footer_lines), attach_paths


def _post_comment(
    *,
    repo_url: str,
    issue_number: int,
    body: str,
    attach_paths: list[str],
    marker: str,
) -> None:
    _run_gh(
        ["gh", "issue", "comment", str(issue_number), "-R", repo_url],
        body=body,
        attach_paths=attach_paths,
        error_message="コメント追加に失敗しました",
        find_existing=lambda: (
            "" if _is_comment_posted(repo_url, issue_number, marker) else None
        ),
    )


def _issue_labels(info: dict[str, Any]) -> list[_Label]:
    """Backlogの課題の状態と種別をGitHubのIssueラベルとして取得する. バリデーションを行う."""
    labels = []
    for prefix, item in (
        (_STATUS_LABEL_PREFIX, info.get("status")),
        (_ISSUE_TYPE_LABEL_PREFIX, info.get("issueType")),
    ):
        if item is None:
            continue
        name = prefix + item["name"]
        # ghの--labelはカンマ区切りで複数のラベルとして解釈される
        if "," in name:
            raise CopyToGithubError(
                f"{info['issueKey']}: ラベル名にカンマを含めることはできません: {name}"
            )
        if len(name) > _MAX_LABEL_NAME_LENGTH:
            raise CopyToGithubError(
                f"{info['issueKey']}: ラベル名が長すぎます"
                f"({len(name)}文字. 上限は{_MAX_LABEL_NAME_LENGTH}文字): {name}"
            )
        match = _LABEL_COLOR_RE.fullmatch(item.get("color") or "")
        if match is None:
            raise CopyToGithubError(
                f"{info['issueKey']}: ラベルの色が不正です: {item.get('color')!r}"
            )
        labels.append(_Label(name, match.group(1).lower(), _LABEL_DESCRIPTIONS[prefix]))
    return labels


def _label_args(flag: str, label_names: list[str]) -> list[str]:
    return [arg for name in label_names for arg in (flag, name)]


def _ensure_labels(repo_url: str, labels: list[_Label]) -> None:
    """存在しない, または色や説明が異なるラベルを作成, 更新する."""
    output = _run_gh_query(
        [
            "gh",
            "label",
            "list",
            "-R",
            repo_url,
            "--limit",
            str(_LABEL_LIST_LIMIT),
            "--json",
            "name,color,description",
        ]
    )
    # GitHubのラベル名は大文字小文字を区別しない
    existing = {
        label["name"].casefold(): (label["color"].lower(), label["description"])
        for label in json.loads(output)
    }
    for label in labels:
        if existing.get(label.name.casefold()) == (label.color, label.description):
            continue
        _run_gh(
            [
                "gh",
                "label",
                "create",
                label.name,
                "-R",
                repo_url,
                "--color",
                label.color,
                "--description",
                label.description,
                "--force",
            ],
            error_message="ラベルの作成に失敗しました",
        )
        print(f"ラベル: {label.name} を作成しました")


def _issue_marker(issue_key: str, issue_id: int) -> str:
    return f"Backlog課題: {issue_key} (issueId: {issue_id})"


def _comment_marker(issue_key: str, comment_id: int) -> str:
    return f"Backlogコメント: {issue_key} (commentId: {comment_id})"


def _has_marker(body: str, marker: str) -> bool:
    # フッターは _render_body で引用記法の行として出力される
    return f"> {marker}" in body.splitlines()


def _find_created_issue_url(repo_url: str, marker: str) -> str | None:
    output = _run_gh_query(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo_url,
            "--state",
            "all",
            "--limit",
            str(_EXISTENCE_CHECK_ISSUE_LIMIT),
            "--json",
            "url,body",
        ]
    )
    for issue in json.loads(output):
        if _has_marker(issue["body"], marker):
            return issue["url"]
    return None


def _is_comment_posted(repo_url: str, issue_number: int, marker: str) -> bool:
    output = _run_gh_query(
        ["gh", "issue", "view", str(issue_number), "-R", repo_url, "--json", "comments"]
    )
    return any(_has_marker(c["body"], marker) for c in json.loads(output)["comments"])


def _issue_level_attachments(
    attachment_by_id: dict[int, dict[str, Any]], comments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # コメントのchangeLogで特定の添付ファイルがどのコメントで追加されたか分かる場合は
    # そのコメント側にのみ添付し, 課題本体とコメントで二重に添付されないようにする.
    comment_attached_ids = {
        entry["attachmentInfo"]["id"]
        for comment in comments
        for entry in (comment.get("changeLog") or [])
        if _is_added_attachment_entry(entry)
    }
    return [a for a in attachment_by_id.values() if a["id"] not in comment_attached_ids]


def _is_attachable(name: str, size: int | None) -> bool:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return (
        extension in _ATTACHABLE_EXTENSIONS
        and size is not None
        and size <= _ATTACHABLE_MAX_SIZE
    )


def _is_oversized(size: int | None) -> bool:
    return size is not None and size > _GITHUB_MAX_FILE_SIZE


def _is_added_attachment_entry(entry: dict[str, Any]) -> bool:
    return (
        entry.get("field") == "attachment"
        and bool(entry.get("attachmentInfo"))
        and entry.get("originalValue") is None
        and entry.get("newValue") is not None
    )


def _comment_attachments(
    comment: dict[str, Any], attachment_by_id: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for entry in comment.get("changeLog") or []:
        if not _is_added_attachment_entry(entry):
            continue
        attachment_info = entry["attachmentInfo"]
        attachment = attachment_by_id.get(attachment_info["id"])
        if attachment is None:
            # 課題情報の添付ファイル一覧に無いものは, 後から削除された添付ファイル.
            attachment = {**attachment_info, "deleted": True}
        result.append(attachment)
    return result


def _resolve_attachments(
    attachments: list[dict[str, Any]],
    issue_dir: Path,
    project_key: str,
    issue_key: str,
    repo_branch: str,
) -> tuple[list[str], list[str]]:
    attach_paths: list[str] = []
    link_lines: list[str] = []

    for attachment in attachments:
        label = _escape_link_text(attachment["name"])
        if attachment.get("deleted"):
            link_lines.append(f"{label} (削除済み)")
            continue

        relative_path = attachment_relative_path(attachment)
        if _is_attachable(attachment["name"], attachment.get("size")):
            local_path = issue_dir / relative_path
            if local_path.exists():
                attach_paths.append(str(local_path))
            else:
                print(
                    f"警告: 添付ファイル '{local_path}' が見つからないため添付をスキップします"
                )
            continue

        url = _build_attachment_link(project_key, issue_key, relative_path, repo_branch)
        link_lines.append(f"[{label}]({url})")

    return attach_paths, link_lines


def _build_attachment_link(
    project_key: str, issue_key: str, relative_path: Path, repo_branch: str
) -> str:
    # macOSのgitは既定 (core.precomposeunicode=true) でファイル名をNFCにしてコミットするため,
    # リンクもNFCに揃える.
    segments = [
        urllib.parse.quote(unicodedata.normalize("NFC", s))
        for s in (repo_branch, project_key, issue_key, *relative_path.parts)
    ]
    return "../blob/" + "/".join(segments)


def _escape_link_text(text: str) -> str:
    return _LINK_TEXT_SPECIAL_RE.sub(r"\\\1", text)


def _render_body(main_text: str, link_lines: list[str], footer_lines: list[str]) -> str:
    sections = []
    if main_text.strip():
        sections.append(main_text)
    if link_lines:
        sections.append("\n".join(f"- {line}" for line in link_lines))
    footer = "\n".join(f"> {line}" for line in footer_lines)
    sections.append(footer)
    return "\n\n---\n\n".join(sections)


def _convert_text(text: str, text_formatting_rule: str, domain: str | None) -> str:
    if text_formatting_rule == "backlog":
        text = convert_backlog_notation_to_markdown(text, domain=domain)
    return _escape_autolinks(text)


def _escape_autolinks(text: str) -> str:
    """GitHubの自動リンクを防ぐため, @と#の直後にZWSPを挿入する.

    @はGitHub上の無関係なユーザーへのメンションに, #123はコピー先リポジトリの
    無関係なIssueへの参照になってしまう.
    コードブロック, インラインコード内は自動リンクされないため対象外とする.
    """
    output_lines: list[str] = []
    open_fence: str | None = None
    for line in text.split("\n"):
        fence_match = _FENCE_RE.match(line)
        if open_fence is not None:
            if (
                fence_match
                and fence_match.group(1)[0] == open_fence[0]
                and len(fence_match.group(1)) >= len(open_fence)
            ):
                open_fence = None
            output_lines.append(line)
            continue
        if fence_match:
            open_fence = fence_match.group(1)
            output_lines.append(line)
            continue
        output_lines.append(_escape_autolinks_outside_inline_code(line))
    return "\n".join(output_lines)


def _escape_autolinks_outside_inline_code(line: str) -> str:
    parts: list[str] = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(line):
        parts.append(_insert_zero_width_space(line[pos : match.start()]))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(_insert_zero_width_space(line[pos:]))
    return "".join(parts)


def _insert_zero_width_space(text: str) -> str:
    text = _MENTION_RE.sub("@" + _ZERO_WIDTH_SPACE, text)
    return _ISSUE_REFERENCE_RE.sub("#" + _ZERO_WIDTH_SPACE, text)


def _run_gh(
    args: list[str],
    *,
    body: str | None = None,
    attach_paths: list[str] | None = None,
    error_message: str,
    find_existing: Callable[[], str | None] | None = None,
) -> str:
    """変更系のghコマンドを実行する.

    `body` を指定した場合は `--body-file` で, `attach_paths` は `--attach` で渡す.
    `find_existing` は前回の試行が実は成功していたかを確認する関数で, 成功していた場合は
    実行が成功した場合の標準出力に相当する値を, そうでなければ None を返す.
    サーバー側では成功しているのにエラーになるケースで重複作成しないよう, リトライ前に呼ぶ.
    """
    attach_args = [arg for path in attach_paths or [] for arg in ("--attach", path)]
    if body is None:
        return _run_gh_with_retry([*args, *attach_args], error_message, find_existing)

    if len(body) > _MAX_BODY_LENGTH:
        raise CopyToGithubError(
            f"{error_message}: 本文が長すぎます({len(body)}文字. 上限は{_MAX_BODY_LENGTH}文字)"
        )

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(body)
        body_path = f.name

    try:
        return _run_gh_with_retry(
            [*args, "--body-file", body_path, *attach_args],
            error_message,
            find_existing,
        )
    finally:
        os.unlink(body_path)


def _run_gh_with_retry(
    full_args: list[str],
    error_message: str,
    find_existing: Callable[[], str | None] | None,
) -> str:
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        _throttle()
        result = subprocess.run(full_args, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout

        stderr = result.stderr.strip()
        is_rate_limit_error = _is_retryable_error(stderr)
        if not is_rate_limit_error and _is_non_retryable_error(stderr):
            raise CopyToGithubError(f"{error_message}: {stderr}")

        max_retries = (
            _MAX_RATE_LIMIT_RETRIES if is_rate_limit_error else _MAX_GENERIC_RETRIES
        )
        if attempt >= max_retries:
            raise CopyToGithubError(f"{error_message}: {stderr}")

        delay = _retry_delay_seconds(attempt)
        reason = "レート制限等" if is_rate_limit_error else "一時的なエラー"
        print(
            f"警告: {reason}により失敗したため {delay:.0f} 秒後に再試行します"
            f" ({attempt + 1}/{max_retries}回目): {stderr}",
            file=sys.stderr,
        )
        time.sleep(delay)

        if find_existing is not None:
            existing = _find_existing_or_raise(find_existing, error_message, stderr)
            if existing is not None:
                print(
                    "警告: 失敗したと思われた前回の試行が成功していたため, 再試行しません",
                    file=sys.stderr,
                )
                return existing

    raise AssertionError("unreachable")


def _find_existing_or_raise(
    find_existing: Callable[[], str | None], error_message: str, stderr: str
) -> str | None:
    try:
        return find_existing()
    except CopyToGithubError as e:
        # 確認できないまま再試行すると重複作成の恐れがあるため, 中断して利用者に委ねる
        raise CopyToGithubError(
            f"{error_message}: {stderr}\n"
            f"また, 失敗した試行が実際には成功していたかを確認できませんでした. "
            f"GitHub上で作成済みでないか確認してから再実行してください: {e}"
        ) from e


def _run_gh_query(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CopyToGithubError(
            f"{' '.join(args)} に失敗しました: {result.stderr.strip()}"
        )
    return result.stdout


def _throttle() -> None:
    global _last_request_monotonic
    now = time.monotonic()
    if _last_request_monotonic is not None:
        wait = _MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_monotonic)
        if wait > 0:
            time.sleep(wait)
    _last_request_monotonic = time.monotonic()


def _is_retryable_error(stderr: str) -> bool:
    return any(pattern.search(stderr) for pattern in _RETRYABLE_ERROR_PATTERNS)


def _is_non_retryable_error(stderr: str) -> bool:
    return any(pattern.search(stderr) for pattern in _NON_RETRYABLE_ERROR_PATTERNS)


def _retry_delay_seconds(attempt: int) -> float:
    delay = min(_RETRY_BASE_DELAY_SECONDS * (2**attempt), _RETRY_MAX_DELAY_SECONDS)
    return delay + random.uniform(0, delay * 0.1)


def _parse_issue_number(output: str) -> int:
    for line in reversed(output.strip().splitlines()):
        match = _ISSUE_NUMBER_RE.search(line)
        if match:
            return int(match.group(1))
    raise CopyToGithubError(f"Issue番号をURLから取得できませんでした: {output!r}")


def _load_record(path: Path) -> dict[str, Any]:
    if path.exists():
        return read_json(path)
    return {}
