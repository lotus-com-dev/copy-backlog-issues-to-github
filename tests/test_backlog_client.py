from __future__ import annotations

from pathlib import Path
from typing import Any

import niquests
import pytest

from copy_backlog_issues_to_github import backlog_client
from copy_backlog_issues_to_github.backlog_client import BacklogApiError, BacklogClient


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        chunks: list[bytes | Exception] | None = None,
    ) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self.headers = headers or {}
        self.text = "error body"
        self._json_data = json_data
        self._chunks = chunks or []
        self.closed = False

    def json(self) -> Any:
        return self._json_data

    def iter_content(self, chunk_size: int) -> Any:
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(
        self, url: str, *, params: dict[str, Any], stream: bool, timeout: Any
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "params": dict(params), "stream": stream, "timeout": timeout}
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(backlog_client.time, "sleep", lambda s: recorded.append(s))
    return recorded


def _client(session: FakeSession) -> BacklogClient:
    client = BacklogClient("example.backlog.jp", "secret")
    client._session = session  # type: ignore[assignment]
    return client


def test_request_adds_api_key_and_base_url(sleeps: list[float]) -> None:
    session = FakeSession([FakeResponse(json_data={"id": 1})])

    assert _client(session).get_project("PROJ") == {"id": 1}
    assert session.calls[0]["url"] == "https://example.backlog.jp/api/v2/projects/PROJ"
    assert session.calls[0]["params"]["apiKey"] == "secret"
    assert session.calls[0]["timeout"] == backlog_client._TIMEOUT_SECONDS


def test_get_all_issues_pages_by_offset(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(backlog_client, "_PAGE_SIZE", 2)
    session = FakeSession(
        [
            FakeResponse(json_data=[{"id": 1}, {"id": 2}]),
            FakeResponse(json_data=[{"id": 3}]),
        ]
    )

    issues = _client(session).get_all_issues(10)

    assert [i["id"] for i in issues] == [1, 2, 3]
    assert [c["params"]["offset"] for c in session.calls] == [0, 2]
    assert session.calls[0]["params"]["projectId[]"] == 10


def test_get_all_comments_pages_by_min_id(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(backlog_client, "_PAGE_SIZE", 2)
    session = FakeSession(
        [
            FakeResponse(json_data=[{"id": 5}, {"id": 8}]),
            FakeResponse(json_data=[]),
        ]
    )

    comments = _client(session).get_all_comments("PROJ-1")

    assert [c["id"] for c in comments] == [5, 8]
    assert "minId" not in session.calls[0]["params"]
    # minIdに指定したIDのコメントはレスポンスに含まれないため, 最後のIDをそのまま指定する
    assert session.calls[1]["params"]["minId"] == 8


def test_retries_after_429_until_reset(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(backlog_client.time, "time", lambda: 1000)
    rate_limited = FakeResponse(status_code=429, headers={"X-RateLimit-Reset": "1010"})
    session = FakeSession([rate_limited, FakeResponse(json_data={"id": 1})])

    assert _client(session).get_issue("PROJ-1") == {"id": 1}
    assert len(session.calls) == 2
    assert sleeps == [11]
    assert rate_limited.closed


def test_waits_when_remaining_is_low(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(backlog_client.time, "time", lambda: 1000)
    session = FakeSession(
        [
            FakeResponse(
                json_data={"id": 1},
                headers={"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "1005"},
            )
        ]
    )

    _client(session).get_issue("PROJ-1")

    assert sleeps == [6]


def test_raises_on_error_status(sleeps: list[float]) -> None:
    session = FakeSession([FakeResponse(status_code=404)])

    with pytest.raises(BacklogApiError, match="404"):
        _client(session).get_issue("PROJ-1")


def test_download_attachment_streams_and_closes(
    tmp_path: Path, sleeps: list[float]
) -> None:
    response = FakeResponse(chunks=[b"ab", b"cd"])
    session = FakeSession([response])
    path = tmp_path / "7" / "a.bin"

    _client(session).download_attachment("PROJ-1", 7, path, expected_size=4)

    assert path.read_bytes() == b"abcd"
    assert sleeps == []
    assert session.calls[0]["stream"] is True
    assert session.calls[0]["url"].endswith("/issues/PROJ-1/attachments/7")
    assert response.closed


def test_gives_up_after_too_many_429(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(backlog_client, "_MAX_RATE_LIMIT_RETRIES", 2)
    session = FakeSession([FakeResponse(status_code=429) for _ in range(3)])

    with pytest.raises(BacklogApiError, match="レート制限"):
        _client(session).get_issue("PROJ-1")

    assert len(session.calls) == 3


def test_retries_server_error_with_exponential_backoff(sleeps: list[float]) -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=503),
            FakeResponse(status_code=502),
            FakeResponse(json_data={"id": 1}),
        ]
    )

    assert _client(session).get_issue("PROJ-1") == {"id": 1}
    assert sleeps == [10.0, 20.0]


def test_retries_connection_error_and_timeout(sleeps: list[float]) -> None:
    session = FakeSession(
        [
            niquests.exceptions.ConnectionError("connection reset"),
            niquests.exceptions.Timeout("read timed out"),
            FakeResponse(json_data={"id": 1}),
        ]
    )

    assert _client(session).get_issue("PROJ-1") == {"id": 1}
    assert sleeps == [10.0, 20.0]


def test_gives_up_after_max_retries_and_masks_api_key(
    sleeps: list[float],
) -> None:
    session = FakeSession(
        [
            niquests.exceptions.ConnectionError("failed: https://x/?apiKey=secret")
            for _ in range(4)
        ]
    )

    with pytest.raises(BacklogApiError) as excinfo:
        _client(session).get_issue("PROJ-1")

    assert len(session.calls) == 4  # 初回 + リトライ3回
    assert sleeps == [10.0, 20.0, 40.0]
    assert "secret" not in str(excinfo.value)
    assert "ConnectionError" in str(excinfo.value)


def test_does_not_retry_client_error(sleeps: list[float]) -> None:
    session = FakeSession([FakeResponse(status_code=400)])

    with pytest.raises(BacklogApiError, match="400"):
        _client(session).get_issue("PROJ-1")

    assert len(session.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "first_response",
    [
        # 受信途中で接続が切れた
        FakeResponse(
            chunks=[b"ab", niquests.exceptions.ChunkedEncodingError("broken")]
        ),
        FakeResponse(chunks=[b"ab", niquests.exceptions.ConnectionError("reset")]),
        # 途中までしか受信できなかった
        FakeResponse(chunks=[b"ab"]),
    ],
)
def test_download_attachment_retries_interrupted_download(
    tmp_path: Path, sleeps: list[float], first_response: FakeResponse
) -> None:
    session = FakeSession([first_response, FakeResponse(chunks=[b"ab", b"cd"])])
    path = tmp_path / "a.bin"

    _client(session).download_attachment("PROJ-1", 7, path, expected_size=4)

    assert path.read_bytes() == b"abcd"
    assert sleeps == [10.0]
    assert first_response.closed
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.bin"]


def test_download_attachment_gives_up_after_max_retries(
    tmp_path: Path, sleeps: list[float]
) -> None:
    session = FakeSession([FakeResponse(chunks=[b"ab"]) for _ in range(4)])
    path = tmp_path / "a.bin"

    with pytest.raises(BacklogApiError, match="SizeMismatchError"):
        _client(session).download_attachment("PROJ-1", 7, path, expected_size=4)

    assert len(session.calls) == 4  # 初回 + リトライ3回
    assert sleeps == [10.0, 20.0, 40.0]
    assert list(tmp_path.iterdir()) == []


def test_download_attachment_does_not_retry_api_error(
    tmp_path: Path, sleeps: list[float]
) -> None:
    session = FakeSession([FakeResponse(status_code=404)])

    with pytest.raises(BacklogApiError, match="404"):
        _client(session).download_attachment(
            "PROJ-1", 7, tmp_path / "a.bin", expected_size=4
        )

    assert len(session.calls) == 1
