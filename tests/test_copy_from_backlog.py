from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from copy_backlog_issues_to_github import copy_from_backlog as module
from copy_backlog_issues_to_github.common import atomic_write_chunks


class FakeBacklogClient:
    def __init__(self, attachment_contents: dict[int, bytes] | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.attachment_contents = attachment_contents or {}

    def get_project(self, project_key: str) -> dict[str, Any]:
        self.calls.append(("get_project", project_key))
        return {"id": 10, "projectKey": project_key}

    def get_all_issues(self, project_id: int) -> list[dict[str, Any]]:
        self.calls.append(("get_all_issues", project_id))
        return [{"issueKey": "P-1"}, {"issueKey": "P-2"}]

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        self.calls.append(("get_issue", issue_key))
        return {
            "issueKey": issue_key,
            "attachments": [
                {"id": 1, "name": "a.png", "size": 3},
                {"id": 2, "name": "../evil.txt", "size": 4},
            ],
        }

    def get_all_comments(self, issue_key: str) -> list[dict[str, Any]]:
        self.calls.append(("get_all_comments", issue_key))
        return []

    def download_attachment(
        self,
        issue_key: str,
        attachment_id: int,
        path: Path,
        *,
        expected_size: int | None,
    ) -> None:
        self.calls.append(("download", issue_key, attachment_id))
        atomic_write_chunks(
            path, [self.attachment_contents[attachment_id]], expected_size=expected_size
        )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: FakeBacklogClient,
    *,
    issue_keys: list[str] | None = None,
    download: bool = True,
) -> None:
    monkeypatch.setattr(module, "BacklogClient", lambda domain, api_key: client)
    module.copy_from_backlog(
        domain="example.backlog.jp",
        api_key="secret",
        working_dir=str(tmp_path),
        project_key="PROJ",
        issue_keys=issue_keys,
        download=download,
    )


def test_fetches_all_files_and_downloads_attachments_from_issue_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeBacklogClient({1: b"png", 2: b"evil"})

    _run(monkeypatch, tmp_path, client, issue_keys=["P-1"])

    project_dir = tmp_path / "PROJ"
    assert json.loads((project_dir / "project.json").read_text())["id"] == 10
    assert (project_dir / "issues.json").exists()
    issue_dir = project_dir / "P-1"
    assert (issue_dir / "info.json").exists()
    assert (issue_dir / "comments.json").exists()
    assert not (issue_dir / "attachments.json").exists()
    assert (issue_dir / "1" / "a.png").read_bytes() == b"png"
    # ディレクトリ部分は除去され, 課題ディレクトリ内に保存される
    assert (issue_dir / "2" / "evil.txt").read_bytes() == b"evil"
    assert not (project_dir / "evil.txt").exists()
    assert not (issue_dir / "evil.txt").exists()
    assert not (project_dir / "P-2").exists()


def test_skips_existing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run(monkeypatch, tmp_path, FakeBacklogClient({1: b"png", 2: b"evil"}))

    second_client = FakeBacklogClient()
    _run(monkeypatch, tmp_path, second_client)

    assert second_client.calls == []


def test_no_download_does_not_download_attachments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeBacklogClient()

    _run(monkeypatch, tmp_path, client, issue_keys=["P-1"], download=False)

    assert not any(call[0] == "download" for call in client.calls)
    assert not (tmp_path / "PROJ" / "P-1" / "1").exists()


def test_size_mismatch_leaves_no_file_and_resumes_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 1回目: a.png のダウンロードが途中で切れた(サイズ不一致)
    with pytest.raises(OSError):
        _run(monkeypatch, tmp_path, FakeBacklogClient({1: b"pn", 2: b"evil"}))

    issue_dir = tmp_path / "PROJ" / "P-1"
    assert not (issue_dir / "1").exists() or list((issue_dir / "1").iterdir()) == []
    assert sorted(p.name for p in issue_dir.iterdir() if p.is_file()) == [
        "comments.json",
        "info.json",
    ]

    # 2回目: 取得済みのJSONはスキップし, 添付ファイルのダウンロードから再開する
    client = FakeBacklogClient({1: b"png", 2: b"evil"})
    _run(monkeypatch, tmp_path, client, issue_keys=["P-1"])

    assert client.calls == [("download", "P-1", 1), ("download", "P-1", 2)]
    assert (issue_dir / "1" / "a.png").read_bytes() == b"png"


def test_same_name_attachments_do_not_collide(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeBacklogClient({1: b"first", 2: b"second"})

    def get_issue(issue_key: str) -> dict[str, Any]:
        return {
            "issueKey": issue_key,
            "attachments": [
                {"id": 1, "name": "image.png", "size": 5},
                {"id": 2, "name": "image.png", "size": 6},
            ],
        }

    client.get_issue = get_issue  # type: ignore[method-assign]
    _run(monkeypatch, tmp_path, client, issue_keys=["P-1"])

    issue_dir = tmp_path / "PROJ" / "P-1"
    assert (issue_dir / "1" / "image.png").read_bytes() == b"first"
    assert (issue_dir / "2" / "image.png").read_bytes() == b"second"
