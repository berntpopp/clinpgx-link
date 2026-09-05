"""Bounded asynchronous clients for approved ClinPGx-linked HTTP sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from clinpgx_link.api.cache import ResponseCache, request_cache_key
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore, canonical_media_type
from clinpgx_link.exceptions import (
    DataValidationError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.models import SourceInfo, SourceResponse

_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_COLLECTION_PATHS = frozenset(
    {
        "/data/pathway",
        "/data/gene",
        "/data/chemical",
        "/data/disease",
        "/data/variant/",
        "/data/literature",
        "/data/guidelineAnnotation",
        "/data/label",
        "/data/summaryAnnotation",
        "/data/variantAnnotation",
        "/data/ontologyTerm",
        "/data/dataAnnotation",
        "/data/connection",
    }
)
_ACCEPT = {
    "json": "application/json",
    "jsonld": "application/ld+json",
    "html": "text/html",
    "text": "text/plain",
}


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        raise DataValidationError("Source returned invalid JSON.") from exc


class RequestScheduler:
    """Space every attempt, including retries and both approved origins."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if requests_per_second <= 0 or requests_per_second > 2:
            raise InvalidInputError(
                "Source request rate must be greater than zero and at most two."
            )
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            delay = self._next - self._clock()
            if delay > 0:
                await self._sleep(delay)
            dispatched = self._clock()
            self._next = max(self._next, dispatched) + self._interval


def _origin(url: httpx.URL | str) -> str | None:
    try:
        parsed = urlsplit(str(url))
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.netloc != parsed.hostname
    ):
        return None
    return f"{parsed.scheme}://{parsed.hostname}"


def _validate_relative_path(path: str, *, cpic: bool) -> None:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "//" in path
        or any(marker in path for marker in ("?", "#", "\\", "%", "{"))
        or len(path) > 4096
    ):
        raise InvalidInputError("Unsupported upstream path.", field="operation")
    if not cpic and not (
        path.startswith("/data/")
        or path.startswith("/report/")
        or path.startswith("/site/")
        or path == "/infobutton"
    ):
        raise InvalidInputError("Unsupported upstream path.", field="operation")


class AsyncWorker(Protocol):
    """Injected execution policy; source clients do not depend on an MCP boundary."""

    def __call__[T](
        self, function: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> Awaitable[T]: ...


class ClinPGxClient:
    """Fetch complete source bodies under shared rate, time, size and origin bounds."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        content_store: ContentStore | None = None,
        *,
        scheduler: RequestScheduler | None = None,
        worker: AsyncWorker = asyncio.to_thread,
    ) -> None:
        self._settings = settings
        self.worker = worker
        self._http = http_client or httpx.AsyncClient(follow_redirects=False)
        self.content_store = content_store or ContentStore(
            Path(settings.cache_root) / "content.sqlite",
            max_bytes=settings.cache_max_bytes,
            max_entries=settings.cache_max_entries,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._scheduler = scheduler or RequestScheduler(settings.api_requests_per_second)
        self._cache = ResponseCache(
            max_entries=settings.cache_max_entries,
            max_bytes=settings.cache_max_bytes,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._closed = False

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        representation: str = "json",
    ) -> SourceResponse:
        """Request one registry-bound ClinPGx API or website route."""
        source_kind = "website" if path.startswith("/site/") else "api"
        source_name = "ClinPGx website API" if source_kind == "website" else "ClinPGx REST API"
        allowed_origins = (
            self._settings.website_allowed_origins
            if source_kind == "website"
            else self._settings.api_allowed_origins
        )
        return await self._request(
            "clinpgx",
            self._settings.api_base_url,
            allowed_origins,
            method,
            path,
            params=params,
            form=form,
            representation=representation,
            source_name=source_name,
            data_source=source_kind,
        )

    async def request_cpic(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> SourceResponse:
        """Request one separately registered CPIC reference-data GET route."""
        return await self._request(
            "cpic",
            self._settings.cpic_api_base_url,
            self._settings.cpic_allowed_origins,
            "GET",
            path,
            params=params,
            form=None,
            representation="json",
            source_name="CPIC API",
            data_source="website",
            extra_headers={"prefer": "count=exact", "range-unit": "items"},
        )

    async def _request_verified_text_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        decoder: Callable[[Any], tuple[Any, str]],
    ) -> SourceResponse:
        """Decode one registry-authorized text/plain JSON route before retention."""
        return await self._request(
            "clinpgx",
            self._settings.api_base_url,
            self._settings.website_allowed_origins,
            method,
            path,
            params=params,
            form=None,
            representation="text",
            source_name="ClinPGx website API",
            data_source="website",
            value_decoder=decoder,
            expected_media_types=frozenset({"text/plain"}),
            effective_media_type="application/json",
            cache_variant="verified-text-json",
        )

    async def _request(
        self,
        namespace: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        form: dict[str, Any] | None,
        representation: str,
        source_name: str,
        data_source: str,
        extra_headers: dict[str, str] | None = None,
        value_decoder: Callable[[Any], tuple[Any, str]] | None = None,
        expected_media_types: frozenset[str] | None = None,
        effective_media_type: str | None = None,
        cache_variant: str | None = None,
    ) -> SourceResponse:
        if self._closed:
            raise UpstreamUnavailableError("Source client is closed.")
        method = method.upper()
        _validate_relative_path(path, cpic=namespace == "cpic")
        if method not in {"GET", "POST"} or (method == "POST" and path != "/infobutton"):
            raise InvalidInputError("Unsupported upstream method.", field="operation")
        if representation not in _ACCEPT:
            raise InvalidInputError("Unsupported source representation.", field="representation")
        if _origin(base_url) not in allowed_origins:
            raise UpstreamUnavailableError("Source origin is not approved for this adapter.")
        cache_representation = (
            representation if cache_variant is None else f"{representation}:{cache_variant}"
        )
        key = request_cache_key(namespace, method, path, params, form, cache_representation)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        url = base_url.rstrip("/") + path
        headers = {"accept": _ACCEPT[representation], **(extra_headers or {})}
        try:
            async with asyncio.timeout(self._settings.request_deadline_seconds):
                response, raw = await self._send_with_policy(
                    method,
                    url,
                    params=params,
                    form=form,
                    headers=headers,
                    allowed_origins=allowed_origins,
                    allowed_path_prefix=urlsplit(base_url).path,
                )
        except TimeoutError as exc:
            raise UpstreamUnavailableError("Source request exceeded its deadline.") from exc

        try:
            upstream_media_type = canonical_media_type(
                response.headers.get("content-type", "application/octet-stream")
            )
        except InvalidInputError as exc:
            raise DataValidationError("Source returned an invalid Content-Type.") from exc
        if expected_media_types is not None and upstream_media_type not in expected_media_types:
            raise DataValidationError("Source did not return the required media type.")

        if response.status_code == 404 and path in _COLLECTION_PATHS:
            value: Any = []
            source_pointer: str | None = None
        else:
            self._raise_for_status(response.status_code)
            value, source_pointer = self._decode(
                path, response.status_code, raw, upstream_media_type, representation
            )
        if value_decoder is not None:
            value, source_pointer = value_decoder(value)
        media_type = canonical_media_type(effective_media_type or upstream_media_type)
        source = SourceInfo(
            source=source_name,
            url=str(response.url),
            retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            sha256=hashlib.sha256(raw).hexdigest(),
            data_source=data_source,
        )
        details: dict[str, Any] = {
            "http_status": response.status_code,
            "media_type": media_type,
            "byte_count": len(raw),
            "cache_hit": False,
        }
        if media_type != upstream_media_type:
            details["upstream_media_type"] = upstream_media_type
        if source_pointer is not None:
            details["source_pointer"] = source_pointer
        if content_range := response.headers.get("content-range"):
            details["content_range"] = content_range
        details["content_ref"] = await self.worker(self.content_store.put, raw, source, media_type)
        result = SourceResponse(value, source, details)
        self._cache.put(key, result, size=len(raw))
        return result

    async def _send_with_policy(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        form: dict[str, Any] | None,
        headers: dict[str, str],
        allowed_origins: tuple[str, ...],
        allowed_path_prefix: str,
    ) -> tuple[httpx.Response, bytes]:
        retries = 0
        redirects = 0
        current_url = url
        current_params = params
        while True:
            await self._scheduler.acquire()
            request = self._http.build_request(
                method,
                current_url,
                params=current_params,
                data=form,
                headers=headers,
                timeout=self._settings.request_timeout_seconds,
            )
            try:
                response = await self._http.send(request, stream=True)
                raw = await self._read_complete(response)
            except httpx.HTTPError as exc:
                if retries < 2:
                    retries += 1
                    continue
                raise UpstreamUnavailableError("Source transport is unavailable.") from exc
            if response.status_code in _REDIRECT_STATUS:
                location = response.headers.get("location")
                await response.aclose()
                if location is None or redirects >= self._settings.max_redirects:
                    raise UpstreamUnavailableError("Source redirect policy was not satisfied.")
                try:
                    target = response.url.join(location)
                except (httpx.InvalidURL, ValueError) as exc:
                    raise UpstreamUnavailableError(
                        "Source redirect policy was not satisfied."
                    ) from exc
                target_path = target.path
                if _origin(target) not in allowed_origins or not (
                    target_path == allowed_path_prefix
                    or target_path.startswith(allowed_path_prefix + "/")
                ):
                    raise UpstreamUnavailableError("Source redirect left its approved origin.")
                redirects += 1
                current_url = str(target)
                current_params = None
                if response.status_code == 303:
                    method = "GET"
                    form = None
                continue
            if response.status_code in _RETRYABLE_STATUS and retries < 2:
                retries += 1
                await response.aclose()
                continue
            return response, raw

    async def _read_complete(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdecimal() and int(declared) > self._settings.max_response_bytes:
            await response.aclose()
            raise ResponseTooLargeError(
                "Complete source response exceeds the configured limit.",
                hint="Use a narrower verified filter or an indexed dataset equivalent.",
            )
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._settings.max_response_bytes:
                await response.aclose()
                raise ResponseTooLargeError(
                    "Complete source response exceeds the configured limit.",
                    hint="Use a narrower verified filter or an indexed dataset equivalent.",
                )
        await response.aclose()
        return bytes(body)

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status < 400:
            return
        if status == 404:
            raise NotFoundError("Source record was not found.")
        if status in {400, 406, 415, 422}:
            raise InvalidInputError("Source rejected the validated request.")
        if status == 429:
            raise RateLimitedError("Source request was rate limited.", subtype="upstream_throttle")
        raise UpstreamUnavailableError("Source returned an unavailable status.")

    @staticmethod
    def _decode(
        path: str,
        status: int,
        raw: bytes,
        media_type: str,
        representation: str,
    ) -> tuple[Any, str]:
        if status == 204:
            if raw:
                raise DataValidationError("No-content source response contained bytes.")
            return None, ""
        if representation == "jsonld" and media_type != "application/ld+json":
            raise DataValidationError("Source did not return the requested JSON-LD representation.")
        if representation == "json" and not (
            media_type == "application/json" or media_type.endswith("+json")
        ):
            raise DataValidationError("Source did not return the requested JSON representation.")
        if representation == "html" and media_type != "text/html":
            raise DataValidationError("Source did not return the requested HTML representation.")
        if representation in {"json", "jsonld"} or media_type.endswith("+json"):
            decoded = _decode_json(raw)
            has_envelope = isinstance(decoded, dict) and set(decoded) >= {"status", "data"}
            if path.startswith("/data/") and not has_envelope:
                raise DataValidationError("Source data response omitted its expected envelope.")
            if has_envelope:
                if decoded["status"] != "success":
                    raise DataValidationError("Source returned a failure envelope.")
                return decoded["data"], "/data"
            return decoded, ""
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise DataValidationError("Source returned invalid UTF-8 text.") from exc
        if path.startswith("/report/literatureId/"):
            stripped = text.strip()
            if re.fullmatch(r"[0-9]+", stripped) is None:
                raise DataValidationError("Source returned an invalid literature identifier.")
            return int(stripped), ""
        return text, ""

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._http.aclose()
        self.content_store.close()


__all__ = ["ClinPGxClient", "RequestScheduler"]
