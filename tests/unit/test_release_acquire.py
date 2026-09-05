"""Security and atomicity tests for official-source streaming downloads."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from clinpgx_link.config import Settings
from clinpgx_link.exceptions import (
    DataValidationError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.releases.acquire import SourceDownloader


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "request_timeout_seconds": 2,
        "request_deadline_seconds": 4,
        "max_archive_bytes": 1024,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _parent(tmp_path: Path) -> Path:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    return parent


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._raw


@pytest.mark.asyncio
async def test_download_direct_preserves_exact_bytes_and_receipt(tmp_path: Path) -> None:
    raw = b"PK\x03\x04exact archive bytes"
    retrieved = datetime(2026, 9, 5, 10, 30, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://api.clinpgx.org/v1/download/file/data/clinical%20annotations.zip"
        )
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            stream=_BytesStream(raw),
            headers={
                "content-type": "application/zip",
                "content-length": str(len(raw)),
                "etag": '"abc"',
                "last-modified": "Fri, 05 Sep 2026 08:00:00 GMT",
                "x-amz-version-id": "version-1",
            },
            request=request,
        )

    destination = _parent(tmp_path) / "archive.zip"
    async with _client(handler) as client:
        downloader = SourceDownloader(_settings(), client=client, utcnow=lambda: retrieved)
        receipt = await downloader.download("data/clinical annotations.zip", destination)

    assert destination.read_bytes() == raw
    assert receipt.destination == destination
    assert receipt.original_url.endswith("data/clinical%20annotations.zip")
    assert receipt.final_url == receipt.original_url
    assert receipt.retrieved_at == retrieved
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.byte_count == len(raw)
    assert receipt.media_type == "application/zip"
    assert receipt.etag == '"abc"'
    assert receipt.last_modified == "Fri, 05 Sep 2026 08:00:00 GMT"
    assert receipt.version_id == "version-1"
    assert receipt.content_length == len(raw)
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_allowed_redirect_is_scheduled_per_attempt(tmp_path: Path) -> None:
    requests: list[str] = []

    class Scheduler:
        calls = 0

        async def acquire(self) -> None:
            self.calls += 1

    scheduler = Scheduler()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "api.clinpgx.org":
            return httpx.Response(
                302,
                headers={"location": "https://s3.pgkb.org/data/archive.zip"},
                request=request,
            )
        return httpx.Response(200, stream=_BytesStream(b"data"), request=request)

    destination = _parent(tmp_path) / "archive.zip"
    async with _client(handler) as client:
        receipt = await SourceDownloader(
            _settings(),
            client=client,
            scheduler=scheduler,  # type: ignore[arg-type]
        ).download("data/archive.zip", destination)

    assert scheduler.calls == 2
    assert requests == [
        "https://api.clinpgx.org/v1/download/file/data/archive.zip",
        "https://s3.pgkb.org/data/archive.zip",
    ]
    assert receipt.final_url == requests[-1]


@pytest.mark.asyncio
async def test_injected_client_credentials_and_headers_are_not_forwarded(tmp_path: Path) -> None:
    observed: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request.headers
        return httpx.Response(200, stream=_BytesStream(b"data"), request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"authorization": "Bearer secret", "x-caller": "secret"},
        cookies={"session": "secret"},
        follow_redirects=False,
    )
    try:
        await SourceDownloader(_settings(), client=client).download(
            "data/archive.zip", _parent(tmp_path) / "archive.zip"
        )
    finally:
        await client.aclose()

    assert observed is not None
    assert "authorization" not in observed
    assert "cookie" not in observed
    assert "x-caller" not in observed


@pytest.mark.parametrize(
    "dataset_id",
    [
        "",
        "archive.zip",
        "data/",
        "data//archive.zip",
        "data/./archive.zip",
        "data/../archive.zip",
        "data/%61rchive.zip",
        "data/archive.zip?x=1",
        "data/archive.zip#x",
        "data/archive\\zip",
        "data/archive\n.zip",
    ],
)
@pytest.mark.asyncio
async def test_rejects_noncanonical_dataset_id(tmp_path: Path, dataset_id: str) -> None:
    destination = _parent(tmp_path) / "archive.zip"
    async with _client(lambda request: httpx.Response(500, request=request)) as client:
        with pytest.raises(InvalidInputError):
            await SourceDownloader(_settings(), client=client).download(dataset_id, destination)


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/data/archive.zip",
        "https://user@api.clinpgx.org/v1/download/file/data/archive.zip",
        "https://api.clinpgx.org:443/v1/download/file/data/archive.zip",
        "https://API.clinpgx.org/v1/download/file/data/archive.zip",
        "https://api.clinpgx.org/v1/download/file/data/other.zip",
        "https://s3.pgkb.org/data/archive.zip#fragment",
    ],
)
@pytest.mark.asyncio
async def test_rejects_unsafe_or_path_changing_redirect(tmp_path: Path, location: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location}, request=request)

    destination = _parent(tmp_path) / "archive.zip"
    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailableError):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination
            )
    assert not destination.exists()


@pytest.mark.asyncio
async def test_rejects_redirect_loop_at_configured_bound(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": str(request.url)}, request=request)

    destination = _parent(tmp_path) / "archive.zip"
    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailableError):
            await SourceDownloader(_settings(max_redirects=1), client=client).download(
                "data/archive.zip", destination
            )
    assert calls == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, RateLimitedError), (404, NotFoundError), (503, UpstreamUnavailableError)],
)
@pytest.mark.asyncio
async def test_translates_http_status(tmp_path: Path, status: int, error: type[Exception]) -> None:
    destination = _parent(tmp_path) / "archive.zip"
    async with _client(
        lambda request: httpx.Response(status, stream=_BytesStream(b"secret body"), request=request)
    ) as client:
        with pytest.raises(error) as caught:
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination
            )
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_rejects_oversized_redirect_metadata(tmp_path: Path) -> None:
    destination = _parent(tmp_path) / "archive.zip"
    async with _client(
        lambda request: httpx.Response(302, headers={"location": "/" + "x" * 5000}, request=request)
    ) as client:
        with pytest.raises(DataValidationError):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination
            )


@pytest.mark.parametrize(
    "headers",
    [
        {"content-encoding": "gzip"},
        {"content-length": "nonnumeric"},
        {"content-length": "3"},
        {"content-length": "2048"},
    ],
)
@pytest.mark.asyncio
async def test_rejects_encoding_or_invalid_declared_size(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    destination = _parent(tmp_path) / "archive.zip"
    async with _client(
        lambda request: httpx.Response(
            200, stream=_BytesStream(b"data"), headers=headers, request=request
        )
    ) as client:
        with pytest.raises((DataValidationError, ResponseTooLargeError)):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination
            )


@pytest.mark.asyncio
async def test_streamed_oversize_and_wrong_digest_preserve_existing_file(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    destination = parent / "archive.zip"
    destination.write_bytes(b"old")
    destination.chmod(0o600)

    async with _client(
        lambda request: httpx.Response(200, stream=_BytesStream(b"x" * 1025), request=request)
    ) as client:
        with pytest.raises(ResponseTooLargeError):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination
            )
    assert destination.read_bytes() == b"old"
    assert list(parent.iterdir()) == [destination]

    async with _client(
        lambda request: httpx.Response(200, stream=_BytesStream(b"new"), request=request)
    ) as client:
        with pytest.raises(DataValidationError):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", destination, expected_sha256="0" * 64
            )
    assert destination.read_bytes() == b"old"
    assert list(parent.iterdir()) == [destination]


class _BlockedStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b"unreachable"


@pytest.mark.asyncio
async def test_stalled_stream_times_out_and_cleans_partial(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    destination = parent / "archive.zip"
    async with _client(
        lambda request: httpx.Response(200, stream=_BlockedStream(), request=request)
    ) as client:
        with pytest.raises(UpstreamUnavailableError):
            await SourceDownloader(_settings(), client=client, stall_timeout_seconds=0.01).download(
                "data/archive.zip", destination
            )
    assert list(parent.iterdir()) == []


@pytest.mark.asyncio
async def test_overall_deadline_includes_scheduler_wait(tmp_path: Path) -> None:
    class BlockedScheduler:
        async def acquire(self) -> None:
            await asyncio.Event().wait()

    destination = _parent(tmp_path) / "archive.zip"
    async with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(UpstreamUnavailableError):
            await SourceDownloader(
                _settings(request_timeout_seconds=0.01, request_deadline_seconds=0.01),
                client=client,
                scheduler=BlockedScheduler(),  # type: ignore[arg-type]
            ).download("data/archive.zip", destination)


@pytest.mark.asyncio
async def test_slow_stream_fails_minimum_throughput_policy(tmp_path: Path) -> None:
    times = iter([0.0, 2.0, 2.0])
    destination = _parent(tmp_path) / "archive.zip"
    async with _client(
        lambda request: httpx.Response(200, stream=_BytesStream(b"data"), request=request)
    ) as client:
        with pytest.raises(UpstreamUnavailableError):
            await SourceDownloader(
                _settings(),
                client=client,
                minimum_bytes_per_second=10,
                throughput_grace_seconds=1,
                clock=lambda: next(times),
            ).download("data/archive.zip", destination)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_partial(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    destination = parent / "archive.zip"
    async with _client(
        lambda request: httpx.Response(200, stream=_BlockedStream(), request=request)
    ) as client:
        task = asyncio.create_task(
            SourceDownloader(_settings(), client=client).download("data/archive.zip", destination)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("parent_mode", [0o770, 0o707])
@pytest.mark.asyncio
async def test_rejects_group_or_world_writable_parent(tmp_path: Path, parent_mode: int) -> None:
    parent = _parent(tmp_path)
    parent.chmod(parent_mode)
    async with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(InvalidInputError):
            await SourceDownloader(_settings(), client=client).download(
                "data/archive.zip", parent / "archive.zip"
            )


@pytest.mark.asyncio
async def test_rejects_relative_destination_parent_symlink_and_destination_links(
    tmp_path: Path,
) -> None:
    real = _parent(tmp_path)
    symlink_parent = tmp_path / "linked"
    symlink_parent.symlink_to(real, target_is_directory=True)
    target = real / "target.zip"
    target.write_bytes(b"old")
    target.chmod(0o600)
    symlink_dest = real / "symlink.zip"
    symlink_dest.symlink_to(target)
    hardlink_dest = real / "hardlink.zip"
    os.link(target, hardlink_dest)

    async with _client(lambda request: httpx.Response(200, request=request)) as client:
        downloader = SourceDownloader(_settings(), client=client)
        for destination in (
            Path("relative.zip"),
            symlink_parent / "archive.zip",
            symlink_dest,
            hardlink_dest,
        ):
            with pytest.raises(InvalidInputError):
                await downloader.download("data/archive.zip", destination)


@pytest.mark.asyncio
async def test_close_only_closes_owned_client(tmp_path: Path) -> None:
    client = _client(
        lambda request: httpx.Response(200, stream=_BytesStream(b"data"), request=request)
    )
    downloader = SourceDownloader(_settings(), client=client)
    await downloader.close()
    assert not client.is_closed
    await client.aclose()

    owned = SourceDownloader(_settings())
    await owned.close()
    assert owned._client.is_closed
