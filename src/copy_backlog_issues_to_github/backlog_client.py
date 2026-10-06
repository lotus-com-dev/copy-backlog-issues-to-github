from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import niquests

from .common import SizeMismatchError, atomic_write_chunks

_PAGE_SIZE = 100
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024

# (接続タイムアウト, 読み込みタイムアウト)
_TIMEOUT_SECONDS = (10, 60)

# 5xx, 接続エラー, タイムアウトは指数バックオフで再試行する.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 10.0
_RETRYABLE_EXCEPTIONS = (
    niquests.exceptions.ConnectionError,
    niquests.exceptions.Timeout,
)
# 添付ファイルの受信途中で接続が切れた場合も, ダウンロードをやり直す.
_RETRYABLE_DOWNLOAD_EXCEPTIONS = (
    *_RETRYABLE_EXCEPTIONS,
    niquests.exceptions.ChunkedEncodingError,
    SizeMismatchError,
)

# 429はリセット時刻まで待って再試行する.
_MAX_RATE_LIMIT_RETRIES = 10


class BacklogApiError(Exception):
    pass


def _sleep_before_retry(retries: int, detail: str) -> None:
    delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (retries - 1))
    print(
        f"警告: Backlog APIのリクエストに失敗したため {delay:.0f} 秒後に再試行します"
        f" ({retries}/{_MAX_RETRIES}回目): {detail}",
        file=sys.stderr,
    )
    time.sleep(delay)


def _seconds_until_reset(reset_header: str | None) -> int:
    if reset_header is None:
        return 60
    try:
        reset_at = int(reset_header)
    except ValueError:
        return 60
    return max(0, reset_at - int(time.time())) + 1


class BacklogClient:
    def __init__(self, domain: str, api_key: str) -> None:
        self._base_url = f"https://{domain}/api/v2"
        self._api_key = api_key
        self._session = niquests.Session()

    def _get(
        self, path: str, *, params: dict[str, Any] | None = None, stream: bool = False
    ) -> niquests.Response:
        url = f"{self._base_url}{path}"
        query = dict(params or {})
        query["apiKey"] = self._api_key

        retries = 0
        rate_limit_retries = 0
        while True:
            try:
                response = self._session.get(
                    url, params=query, stream=stream, timeout=_TIMEOUT_SECONDS
                )
            except _RETRYABLE_EXCEPTIONS as e:
                # 例外メッセージにはAPIキーを含むURLが含まれ得るため伏せる
                error = f"{type(e).__name__}: {self._mask_api_key(str(e))}"
            else:
                if response.status_code == 429:
                    response.close()
                    if rate_limit_retries >= _MAX_RATE_LIMIT_RETRIES:
                        raise BacklogApiError(
                            f"Backlog API error: GET {url} -> レート制限が解除されませんでした"
                        )
                    rate_limit_retries += 1
                    wait_seconds = _seconds_until_reset(
                        response.headers.get("X-RateLimit-Reset")
                    )
                    print(f"レート制限に達しました。{wait_seconds}秒待機します...")
                    time.sleep(wait_seconds)
                    continue

                if response.status_code < 500:
                    if not response.ok:
                        raise BacklogApiError(
                            f"Backlog API error: GET {url} -> {response.status_code} {response.text}"
                        )
                    self._wait_if_rate_limit_is_low(response)
                    return response

                error = f"{response.status_code} {response.text}"
                response.close()

            if retries >= _MAX_RETRIES:
                raise BacklogApiError(
                    f"Backlog API error: GET {url} -> {error}"
                ) from None
            retries += 1
            _sleep_before_retry(retries, f"GET {url} -> {error}")

    def _wait_if_rate_limit_is_low(self, response: niquests.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) <= 1:
            wait_seconds = _seconds_until_reset(
                response.headers.get("X-RateLimit-Reset")
            )
            if wait_seconds > 0:
                print(f"レート制限の残数が少ないため{wait_seconds}秒待機します...")
                time.sleep(wait_seconds)

    def _mask_api_key(self, text: str) -> str:
        return text.replace(self._api_key, "***")

    def get_project(self, project_id_or_key: str) -> dict[str, Any]:
        return self._get(f"/projects/{project_id_or_key}").json()

    def get_issues(
        self, project_id: int, *, count: int, offset: int
    ) -> list[dict[str, Any]]:
        params = {
            "projectId[]": project_id,
            "count": count,
            "offset": offset,
            "sort": "created",
            "order": "asc",
        }
        return self._get("/issues", params=params).json()

    def get_all_issues(self, project_id: int) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.get_issues(project_id, count=_PAGE_SIZE, offset=offset)
            issues.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return issues

    def get_issue(self, issue_id_or_key: str) -> dict[str, Any]:
        return self._get(f"/issues/{issue_id_or_key}").json()

    def get_comments(
        self, issue_id_or_key: str, *, count: int, min_id: int | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"count": count, "order": "asc"}
        if min_id is not None:
            params["minId"] = min_id
        return self._get(f"/issues/{issue_id_or_key}/comments", params=params).json()

    def get_all_comments(self, issue_id_or_key: str) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        min_id: int | None = None
        while True:
            page = self.get_comments(issue_id_or_key, count=_PAGE_SIZE, min_id=min_id)
            comments.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            # minIdに指定したIDのコメントはレスポンスに含まれない
            min_id = page[-1]["id"]
        return comments

    def download_attachment(
        self,
        issue_id_or_key: str,
        attachment_id: int,
        path: Path,
        *,
        expected_size: int | None,
    ) -> None:
        """添付ファイルを `path` にダウンロードする. 受信途中で失敗した場合は最初からやり直す."""
        retries = 0
        while True:
            try:
                atomic_write_chunks(
                    path,
                    self._iter_attachment_content(issue_id_or_key, attachment_id),
                    expected_size=expected_size,
                )
                return
            except _RETRYABLE_DOWNLOAD_EXCEPTIONS as e:
                error = f"{type(e).__name__}: {self._mask_api_key(str(e))}"

            detail = f"添付ファイル {attachment_id} のダウンロード -> {error}"
            if retries >= _MAX_RETRIES:
                raise BacklogApiError(f"Backlog API error: {detail}") from None
            retries += 1
            _sleep_before_retry(retries, detail)

    def _iter_attachment_content(
        self, issue_id_or_key: str, attachment_id: int
    ) -> Iterator[bytes]:
        response = self._get(
            f"/issues/{issue_id_or_key}/attachments/{attachment_id}", stream=True
        )
        try:
            yield from response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE)
        finally:
            response.close()
