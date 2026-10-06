from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backlog_client import BacklogClient
from .common import (
    atomic_write_json,
    attachment_relative_path,
    filter_target_issues,
    read_json,
)


def _fetch_or_skip(path: Path, fetch: Callable[[], Any]) -> Any:
    if path.exists():
        print(f"スキップ: {path} は既に存在するため取得しません")
        return read_json(path)

    data = fetch()
    atomic_write_json(path, data)
    print(f"取得: {path}")
    return data


def copy_from_backlog(
    *,
    domain: str,
    api_key: str,
    working_dir: str,
    project_key: str,
    issue_keys: list[str] | None,
    download: bool,
) -> None:
    client = BacklogClient(domain, api_key)

    project_dir = Path(working_dir) / project_key
    project = _fetch_or_skip(
        project_dir / "project.json",
        lambda: client.get_project(project_key),
    )

    issues = _fetch_or_skip(
        project_dir / "issues.json",
        lambda: client.get_all_issues(project["id"]),
    )

    for issue in filter_target_issues(issues, issue_keys):
        _copy_issue(client, project_dir, issue["issueKey"], download=download)


def _copy_issue(
    client: BacklogClient, project_dir: Path, issue_key: str, *, download: bool
) -> None:
    issue_dir = project_dir / issue_key

    info = _fetch_or_skip(issue_dir / "info.json", lambda: client.get_issue(issue_key))
    _fetch_or_skip(
        issue_dir / "comments.json", lambda: client.get_all_comments(issue_key)
    )

    if download:
        _download_attachments(
            client, issue_dir, issue_key, info.get("attachments") or []
        )


def _download_attachments(
    client: BacklogClient,
    issue_dir: Path,
    issue_key: str,
    attachments: list[dict[str, Any]],
) -> None:
    for attachment in attachments:
        attachment_path = issue_dir / attachment_relative_path(attachment)
        if attachment_path.exists():
            print(f"スキップ: {attachment_path} は既に存在するためダウンロードしません")
            continue

        client.download_attachment(
            issue_key,
            attachment["id"],
            attachment_path,
            expected_size=attachment.get("size"),
        )
        print(f"取得: {attachment_path}")
