"""One HTTP client for every reader: a plain user agent, retries and a cache."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

from walkbike_routes import __version__

USER_AGENT = f"walkbike-routes/{__version__}"
TIMEOUT_S = 300.0


class _HideKeys(logging.Filter):
    """httpx logs each request URL at INFO; mask any ``key=`` value in it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        hidden = re.sub(r"([?&](?:key|api_key|token)=)[^&\s\"']+", r"\1***", message)
        if hidden != message:
            record.msg, record.args = hidden, None
        return True


logging.getLogger("httpx").addFilter(_HideKeys())


class DownloadError(RuntimeError):
    """A source could not be read."""


def _retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(error, httpx.TransportError)


def _redact(text: str, secrets: Mapping[str, str] | None) -> str:
    for value in (secrets or {}).values():
        if value:
            text = text.replace(value, "***")
    return text


class Fetcher:
    """GET and POST with retries, caching each answer by its request.

    Secrets passed in ``secret_params`` are sent but never written to the
    cache key or to disk, so a cached file can be shared without leaking an
    API key, and are scrubbed from error messages.  httpx logs each request
    URL at INFO level, so the command line keeps its logger at WARNING.
    """

    def __init__(
        self, cache: Path, client: httpx.Client | None = None, backoff: wait_base | None = None
    ) -> None:
        self.cache = cache
        self.backoff = backoff or wait_exponential(multiplier=2, max=60)
        self.client = client or httpx.Client(
            timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def json(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        data: Mapping[str, str] | None = None,
        secret_params: Mapping[str, str] | None = None,
        name: str = "",
        check: Callable[[Any], str | None] | None = None,
        timeout_s: float = TIMEOUT_S,
        attempts: int = 4,
    ) -> Any:
        """The JSON answer to a request, from the cache when it has been asked before.

        ``check`` returns a reason when an answer is really an error report;
        such an answer raises :class:`DownloadError` and is not cached.
        ``timeout_s`` and ``attempts`` bound how long one source may hold a run.
        """
        plain = dict(params or {})
        path = self._file(url, plain, data, name)
        cached = self.cached(url, params, data=data, name=name, check=check)
        if cached is not None:
            return cached
        try:
            answer = self._fetch(url, {**plain, **(secret_params or {})}, data, timeout_s, attempts)
        except (httpx.HTTPError, DownloadError) as error:
            text = (
                str(error) if isinstance(error, DownloadError) else f"could not read {url}: {error}"
            )
            message = _redact(text, secret_params)
            if secret_params:
                raise DownloadError(message) from None  # the cause holds the key
            raise DownloadError(message) from error
        problem = check(answer) if check is not None else None
        if problem:
            raise DownloadError(_redact(f"{url}: {problem}", secret_params))
        self.cache.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_text(json.dumps(answer))
        part.replace(path)
        return answer

    def _file(
        self, url: str, params: Mapping[str, str], data: Mapping[str, str] | None, name: str
    ) -> Path:
        request = {"url": url, "params": dict(params), "data": dict(data or {})}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16]
        return self.cache / f"{name or 'response'}-{digest}.json"

    def cached(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        data: Mapping[str, str] | None = None,
        name: str = "",
        check: Callable[[Any], str | None] | None = None,
    ) -> Any:
        """The saved answer to a request, or None when it has not been asked before."""
        path = self._file(url, dict(params or {}), data, name)
        if not path.exists():
            return None
        try:
            saved = json.loads(path.read_text())
        except ValueError:
            saved = None  # a damaged file: ask again
        if saved is None or (check is not None and check(saved)):
            path.unlink()  # or an error report saved before it was recognised as one
            return None
        return saved

    def _fetch(
        self,
        url: str,
        params: Mapping[str, str],
        data: Mapping[str, str] | None,
        timeout_s: float,
        attempts: int,
    ) -> Any:
        response: httpx.Response | None = None
        for attempt in Retrying(
            retry=retry_if_exception(_retryable),
            stop=stop_after_attempt(attempts),
            wait=self.backoff,
            reraise=True,
        ):
            with attempt:
                if data is None:
                    response = self.client.get(url, params=params, timeout=timeout_s)
                else:
                    response = self.client.post(url, params=params, data=data, timeout=timeout_s)
                response.raise_for_status()
        assert response is not None
        try:
            return response.json()
        except ValueError as error:
            snippet = response.text[:200].replace("\n", " ")
            raise DownloadError(f"{url} did not answer with JSON: {snippet}") from error

    def download(self, url: str, name: str, *, timeout_s: float = 600.0) -> Path:
        """A large file saved under the cache, streamed; reused if already there."""
        path = self.cache / name
        if path.exists():
            return path
        self.cache.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        try:
            for attempt in Retrying(
                retry=retry_if_exception(_retryable),
                stop=stop_after_attempt(3),
                wait=self.backoff,
                reraise=True,
            ):
                with attempt, self.client.stream("GET", url, timeout=timeout_s) as response:
                    response.raise_for_status()
                    with part.open("wb") as out:
                        for chunk in response.iter_bytes(1 << 20):
                            out.write(chunk)
        except httpx.HTTPError as error:
            part.unlink(missing_ok=True)
            raise DownloadError(f"could not download {url}: {error}") from error
        part.replace(path)
        return path
