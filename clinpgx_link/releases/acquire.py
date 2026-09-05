"""Bounded streaming acquisition from canonical ClinPGx download paths.

The destination parent is a trusted, same-owner mutation boundary: it must already
exist, be a real private directory, and remain under the operator's control while a
download runs. File inspection, temporary creation, and replacement are performed
relative to an open directory descriptor where the platform supports it.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import secrets
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlsplit

import httpx

from clinpgx_link.api.client import RequestScheduler
from clinpgx_link.config import Settings
from clinpgx_link.exceptions import (
    ClinPGxError,
    DataValidationError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)

_API_ORIGIN = "https://api.clinpgx.org"
_S3_ORIGIN = "https://s3.pgkb.org"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CHUNK_BYTES = 64 * 1024
_MAX_METADATA_BYTES = 4096
_SHA256_LENGTH = 64


@dataclass(frozen=True)
class DownloadReceipt:
    """Observed identity and bounded response metadata for one acquisition."""

    destination: Path
    original_url: str
    final_url: str
    retrieved_at: datetime
    sha256: str
    byte_count: int
    media_type: str
    etag: str | None = None
    last_modified: str | None = None
    version_id: str | None = None
    content_length: int | None = None


def _dataset_path(dataset_id: str) -> tuple[str, str]:
    if (
        not isinstance(dataset_id, str)
        or not dataset_id.startswith("data/")
        or any(character in dataset_id for character in ("%", "?", "#", "\\"))
        or any(ord(character) < 32 or ord(character) == 127 for character in dataset_id)
    ):
        raise InvalidInputError("Dataset ID must be a canonical data/ path.", field="dataset_id")
    parts = dataset_id.split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise InvalidInputError("Dataset ID must be a canonical data/ path.", field="dataset_id")
    encoded = "/".join(quote(part, safe="") for part in parts)
    return f"/v1/download/file/{encoded}", f"/{encoded}"


def _validated_url(value: str, api_path: str, s3_path: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise UpstreamUnavailableError("Source redirect policy was not satisfied.") from exc
    expected_path: str | None = None
    if parsed.hostname == "api.clinpgx.org" and parsed.netloc == "api.clinpgx.org":
        expected_path = api_path
    elif parsed.hostname == "s3.pgkb.org" and parsed.netloc == "s3.pgkb.org":
        expected_path = s3_path
    if (
        parsed.scheme != "https"
        or expected_path is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise UpstreamUnavailableError("Source redirect policy was not satisfied.")
    if value != f"https://{parsed.netloc}{expected_path}":
        raise UpstreamUnavailableError("Source redirect policy was not satisfied.")
    return value


def _expected_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InvalidInputError("Expected digest must be canonical SHA-256.", field="sha256")
    return value


def _open_parent(destination: Path) -> int:
    if not isinstance(destination, Path) or not destination.is_absolute() or destination.name == "":
        raise InvalidInputError("Destination must be an absolute file path.", field="destination")
    parent = destination.parent
    try:
        parent_info = parent.lstat()
        if parent.resolve(strict=True) != parent:
            raise InvalidInputError(
                "Destination parent must be a real private directory.", field="destination"
            )
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_ISLNK(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or parent_info.st_mode & 0o022
        ):
            raise InvalidInputError(
                "Destination parent must be a real private directory.", field="destination"
            )
        descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        if (opened.st_dev, opened.st_ino) != (parent_info.st_dev, parent_info.st_ino):
            os.close(descriptor)
            raise InvalidInputError(
                "Destination parent changed during validation.", field="destination"
            )
        return descriptor
    except InvalidInputError:
        raise
    except OSError as exc:
        raise InvalidInputError("Destination parent is unavailable.", field="destination") from exc


def _validate_destination(parent_fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InvalidInputError("Destination cannot be inspected.", field="destination") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InvalidInputError(
            "Destination must be a regular single-link file.", field="destination"
        )
    return True


def _create_temporary(parent_fd: int, destination_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(16):
        name = f".{destination_name}.{secrets.token_hex(12)}.partial"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise UpstreamUnavailableError("Local acquisition storage is unavailable.") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("temporary entry is not a regular single-link file")
            os.fchmod(descriptor, 0o600)
        except OSError as exc:
            with suppress(OSError):
                os.close(descriptor)
            with suppress(OSError):
                os.unlink(name, dir_fd=parent_fd)
            raise UpstreamUnavailableError("Local acquisition storage is unavailable.") from exc
        return descriptor, name
    raise UpstreamUnavailableError("Local acquisition storage is unavailable.")


def _create_recovery_link(parent_fd: int, destination_name: str) -> str:
    """Retain the admitted destination inode until publication commits."""
    for _attempt in range(16):
        name = f".{destination_name}.{secrets.token_hex(12)}.recovery"
        try:
            os.link(
                destination_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            return name
        except FileExistsError:
            continue
        except OSError as exc:
            raise UpstreamUnavailableError("Local acquisition storage is unavailable.") from exc
    raise UpstreamUnavailableError("Local acquisition storage is unavailable.")


def _valid_timing(value: object, *, allow_zero: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except OverflowError:
        return False
    return math.isfinite(number) and (number >= 0 if allow_zero else number > 0)


def _metadata(headers: httpx.Headers, name: str) -> str | None:
    value = cast(str | None, headers.get(name))
    if value is not None and len(value.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise DataValidationError("Source response metadata exceeds its bound.")
    return value


def _content_length(headers: httpx.Headers, maximum: int) -> int | None:
    value = _metadata(headers, "content-length")
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal():
        raise DataValidationError("Source content length is invalid.")
    length = int(value)
    if length > maximum:
        raise ResponseTooLargeError("Source archive exceeds the configured limit.")
    return length


class SourceDownloader:
    """Download one official registry path without trusting caller-supplied URLs."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        scheduler: RequestScheduler | None = None,
        stall_timeout_seconds: float | None = None,
        minimum_bytes_per_second: float = 1024.0,
        throughput_grace_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            (
                stall_timeout_seconds is not None
                and not _valid_timing(stall_timeout_seconds, allow_zero=False)
            )
            or not _valid_timing(minimum_bytes_per_second, allow_zero=False)
            or not _valid_timing(throughput_grace_seconds, allow_zero=True)
        ):
            raise InvalidInputError("Download timing policy is invalid.")
        self._settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.request_timeout_seconds,
                read=settings.request_timeout_seconds,
            ),
        )
        self._scheduler = scheduler or RequestScheduler(settings.api_requests_per_second)
        self._stall_timeout = stall_timeout_seconds or settings.request_timeout_seconds
        self._minimum_rate = minimum_bytes_per_second
        self._throughput_grace = throughput_grace_seconds
        self._clock = clock
        self._utcnow = utcnow
        self._closed = False

    async def close(self) -> None:
        """Close only the HTTP client created by this downloader."""
        if not self._closed and self._owned_client:
            await self._client.aclose()
        self._closed = True

    async def download(
        self,
        dataset_id: str,
        destination: Path,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadReceipt:
        """Stream, validate, and atomically publish one official source file.

        Publication is accepted after the replacement directory fsync and final
        monotonic deadline check. Cleanup after that commit point is best effort:
        failure can retain a hidden recovery link but cannot turn a durable accepted
        destination into a falsely reported failed acquisition.
        """
        api_path, s3_path = _dataset_path(dataset_id)
        expected = _expected_digest(expected_sha256)
        if self._closed:
            raise UpstreamUnavailableError("Source downloader is closed.")
        started = self._clock()
        if not math.isfinite(started):
            raise UpstreamUnavailableError("Acquisition clock is unavailable.")
        deadline = started + self._settings.request_deadline_seconds
        parent_fd = _open_parent(destination)
        temporary_name: str | None = None
        descriptor: int | None = None
        try:
            _validate_destination(parent_fd, destination.name)
            descriptor, temporary_name = _create_temporary(parent_fd, destination.name)
            try:
                async with asyncio.timeout(self._settings.request_deadline_seconds):
                    receipt = await self._download_to_descriptor(
                        destination,
                        api_path,
                        s3_path,
                        descriptor,
                        expected,
                    )
                    self._check_deadline(deadline)
                    os.fsync(descriptor)
                    self._check_deadline(deadline)
                    os.close(descriptor)
                    descriptor = None
                    destination_exists = _validate_destination(parent_fd, destination.name)
                    self._publish(
                        parent_fd,
                        temporary_name,
                        destination.name,
                        destination_exists=destination_exists,
                        deadline=deadline,
                    )
                    temporary_name = None
                    return receipt
            except TimeoutError as exc:
                raise UpstreamUnavailableError("Source download exceeded its time limit.") from exc
        except asyncio.CancelledError:
            raise
        except ClinPGxError:
            raise
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            raise UpstreamUnavailableError("Source download is unavailable.") from exc
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            with suppress(OSError):
                os.close(parent_fd)

    async def _download_to_descriptor(
        self,
        destination: Path,
        api_path: str,
        s3_path: str,
        descriptor: int,
        expected_sha256: str | None,
    ) -> DownloadReceipt:
        original_url = f"{_API_ORIGIN}{api_path}"
        current_url = original_url
        redirects = 0
        while True:
            await self._scheduler.acquire()
            timeout = self._settings.request_timeout_seconds
            request = httpx.Request(
                "GET",
                current_url,
                headers={"accept-encoding": "identity"},
                extensions={
                    "timeout": {
                        "connect": timeout,
                        "read": timeout,
                        "write": timeout,
                        "pool": timeout,
                    }
                },
            )
            response = await self._client.send(
                request, stream=True, follow_redirects=False, auth=None
            )
            if response.status_code not in _REDIRECTS:
                break
            try:
                location = _metadata(response.headers, "location")
            finally:
                await response.aclose()
            if location is None or redirects >= self._settings.max_redirects:
                raise UpstreamUnavailableError("Source redirect policy was not satisfied.")
            try:
                raw_target = urlsplit(location)
                if raw_target.scheme or raw_target.netloc:
                    _validated_url(location, api_path, s3_path)
                target = response.url.join(location)
            except (httpx.InvalidURL, ValueError) as exc:
                raise UpstreamUnavailableError("Source redirect policy was not satisfied.") from exc
            current_url = _validated_url(str(target), api_path, s3_path)
            redirects += 1

        try:
            self._raise_for_status(response.status_code)
            encoding = _metadata(response.headers, "content-encoding")
            if encoding is not None and encoding.lower() != "identity":
                raise DataValidationError("Source content encoding is unsupported.")
            declared = _content_length(response.headers, self._settings.max_archive_bytes)
            media_type_header = _metadata(response.headers, "content-type") or ""
            media_type = media_type_header.partition(";")[0].strip().lower()
            if len(media_type) > 256:
                raise DataValidationError("Source media type exceeds its bound.")
            digest = hashlib.sha256()
            total = 0
            started = self._clock()
            iterator = response.aiter_raw(_CHUNK_BYTES).__aiter__()
            while True:
                try:
                    async with asyncio.timeout(self._stall_timeout):
                        chunk = await anext(iterator)
                except StopAsyncIteration:
                    break
                total += len(chunk)
                if total > self._settings.max_archive_bytes:
                    raise ResponseTooLargeError("Source archive exceeds the configured limit.")
                digest.update(chunk)
                self._write_all(descriptor, chunk)
                elapsed = self._clock() - started
                if (
                    elapsed >= self._throughput_grace
                    and total / max(elapsed, 1e-9) < self._minimum_rate
                ):
                    raise UpstreamUnavailableError("Source stream is below the minimum throughput.")
            elapsed = self._clock() - started
            if (
                elapsed >= self._throughput_grace
                and total / max(elapsed, 1e-9) < self._minimum_rate
            ):
                raise UpstreamUnavailableError("Source stream is below the minimum throughput.")
            if declared is not None and total != declared:
                raise DataValidationError("Source content length does not match received bytes.")
            actual_digest = digest.hexdigest()
            if expected_sha256 is not None and actual_digest != expected_sha256:
                raise DataValidationError("Source digest does not match the expected identity.")
            retrieved_at = self._utcnow()
            if retrieved_at.tzinfo is None:
                raise UpstreamUnavailableError("Acquisition clock did not return UTC time.")
            return DownloadReceipt(
                destination=destination,
                original_url=original_url,
                final_url=current_url,
                retrieved_at=retrieved_at.astimezone(UTC),
                sha256=actual_digest,
                byte_count=total,
                media_type=media_type,
                etag=_metadata(response.headers, "etag"),
                last_modified=_metadata(response.headers, "last-modified"),
                version_id=_metadata(response.headers, "x-amz-version-id"),
                content_length=declared,
            )
        finally:
            await response.aclose()

    @staticmethod
    def _write_all(descriptor: int, chunk: bytes) -> None:
        view = memoryview(chunk)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]

    def _check_deadline(self, deadline: float) -> None:
        now = self._clock()
        if not math.isfinite(now) or now >= deadline:
            raise UpstreamUnavailableError("Source download exceeded its time limit.")

    def _publish(
        self,
        parent_fd: int,
        temporary_name: str,
        destination_name: str,
        *,
        destination_exists: bool,
        deadline: float,
    ) -> None:
        """Publish with a recoverable old-name state until commit is admitted."""
        recovery_name: str | None = None
        replaced = False
        try:
            if destination_exists:
                recovery_name = _create_recovery_link(parent_fd, destination_name)
                os.fsync(parent_fd)
            self._check_deadline(deadline)
            os.replace(
                temporary_name,
                destination_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            replaced = True
            try:
                self._check_deadline(deadline)
                os.fsync(parent_fd)
                self._check_deadline(deadline)
            except BaseException:
                try:
                    if recovery_name is not None:
                        os.replace(
                            recovery_name,
                            destination_name,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                        )
                        recovery_name = None
                    else:
                        os.unlink(destination_name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except BaseException as exc:
                    raise UpstreamUnavailableError("Source publication recovery failed.") from exc
                raise
            if recovery_name is not None:
                try:
                    os.unlink(recovery_name, dir_fd=parent_fd)
                    recovery_name = None
                except OSError:
                    pass
        finally:
            if recovery_name is not None and not replaced:
                with suppress(OSError):
                    os.unlink(recovery_name, dir_fd=parent_fd)

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if 200 <= status < 300:
            return
        if status == 404:
            raise NotFoundError("Official source file was not found.")
        if status == 429:
            raise RateLimitedError("Official source request was rate limited.")
        raise UpstreamUnavailableError("Official source returned an unavailable status.")


__all__ = ["DownloadReceipt", "SourceDownloader"]
