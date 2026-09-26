"""The one way the builder talks to the internet.

The rules every source gets, whoever wrote the adapter:

- an honest ``User-Agent`` naming the project and where to find it;
- at most one request a second to any one host;
- a cache on disk, keyed by address, that a rebuild reads instead of asking again, and that
  ``--no-network`` builds from alone;
- failures are raised as :class:`FetchError` with the address and the reason, never swallowed.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from packbuilder import VERSION

USER_AGENT = f"xxsm-packbuilder/{VERSION} (+https://github.com/dotStray/xxsm-presets)"


class FetchError(Exception):
    """A source could not be read."""


class Fetcher:
    def __init__(self, cache: pathlib.Path, *, offline: bool = False, delay: float = 1.0, token: str | None = None):
        self.cache = cache
        self.offline = offline
        self.delay = delay
        self.token = token
        self._last: dict[str, float] = {}

    def _cache_path(self, url: str) -> pathlib.Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache / "http" / digest[:2] / digest

    def get(self, url: str, *, fresh: bool = True) -> bytes:
        """The body at ``url``.

        ``fresh=True`` asks the network (and refreshes the cache); ``fresh=False`` is content
        addressed by the URL itself — a picture, a file at a fixed commit — so a cached copy is
        used without asking. Offline, only the cache is read.
        """
        path = self._cache_path(url)
        if path.is_file() and (self.offline or not fresh):
            return path.read_bytes()
        if self.offline:
            raise FetchError(f"{url}: not in the cache, and the build was asked not to use the network.")

        host = urllib.parse.urlparse(url).netloc
        wait = self._last.get(host, 0.0) + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)

        headers = {"User-Agent": USER_AGENT}
        if self.token and host in ("api.github.com",):
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)

        body = self._download(request, url)
        self._last[host] = time.monotonic()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body

    @staticmethod
    def _download(request: urllib.request.Request, url: str) -> bytes:
        """Three tries for a network hiccup; none for an answer that will not change."""
        problem = "no answer"
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                problem = f"HTTP {error.code} {error.reason}"
                if error.code in (401, 403, 404, 410):
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                problem = str(getattr(error, "reason", error))
        raise FetchError(f"{url}: {problem}")

    def get_json(self, url: str, *, fresh: bool = True) -> object:
        body = self.get(url, fresh=fresh)
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise FetchError(f"{url}: the answer was not JSON ({error.msg}).") from error
