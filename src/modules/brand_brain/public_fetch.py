"""Legal public-page fetcher for declared competitors (SPRINT-10 B-2, D-074 ج).

The **only** place in ``brand_brain`` allowed to import ``httpx`` (``pyproject.toml``
per-file-ignores). Everything it does is bounded on purpose:

* host must be in the competitor's ``allowed_hosts`` (owner-entered) — else ``host_not_allowed``;
* closed platforms (:data:`BLOCKED_PLATFORM_HOSTS`) are refused **before any network call**
  — ``blocked_platform``;
* ``robots.txt`` is fetched first (cached 1 h in memory per host) and ``Disallow`` for
  ``User-Agent: *`` **or** ours is honoured — ``robots_disallow``;
* identifying ``User-Agent`` :data:`USER_AGENT`; ``GET`` only; no cookies; no JavaScript;
  redirects are **not** followed (a cross-host redirect is a refusal, same-host 3xx counts as
  ``non_html``); 10 s total timeout — ``timeout``;
* response body streamed and cut at :data:`MAX_BYTES` (512 KiB) — ``too_large``;
* only ``text/html`` is parsed, with the stdlib :mod:`html.parser` — ``non_html``.

A refusal is **data**, not an error: the caller records it as ``estimated`` evidence with
``value={"unavailable": reason}`` so the diagnosis can say "we could not look" instead of
guessing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Final, Literal
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from src.modules.brand_brain.domain.hosts import (
    BLOCKED_PLATFORM_HOSTS,
    host_matches,
    is_blocked_platform,
    normalize_host,
)

USER_AGENT: Final = "AIGrowthOS-BrandBrain/1.0 (+https://aigrowthos.example/contact)"
MAX_BYTES: Final = 512 * 1024
TIMEOUT_SECONDS: Final = 10.0
ROBOTS_TTL_SECONDS: Final = 3600
TEXT_EXCERPT_CHARS: Final = 4000
ROBOTS_MAX_BYTES: Final = 64 * 1024

RefusalReason = Literal[
    "host_not_allowed", "robots_disallow", "too_large", "timeout", "non_html", "blocked_platform"
]

__all__ = [
    "BLOCKED_PLATFORM_HOSTS",
    "MAX_BYTES",
    "USER_AGENT",
    "PublicFetchRefused",
    "PublicPage",
    "PublicPageFetcher",
]


class PublicFetchRefused(Exception):  # noqa: N818 - name mandated by CURRENT_DIRECTIVE §2 B-2
    def __init__(self, reason: RefusalReason, *, host: str | None = None) -> None:
        self.reason: RefusalReason = reason
        self.host = host
        super().__init__(f"public fetch refused: {reason}")


@dataclass(frozen=True)
class PublicPage:
    host: str
    status: int
    title: str | None
    meta_description: str | None
    text_excerpt: str
    fetched_at: datetime


# ------------------------------------------------------------ HTML (stdlib) --
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})


class _TextExtractor(HTMLParser):
    """Collects ``<title>``, ``<meta name=description>`` and visible body text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS and tag != "head":
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("name", "").lower() == "description" and self.description is None:
                self.description = a.get("content", "").strip() or None

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and tag != "head" and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        chunk = " ".join(data.split())
        if chunk:
            self.text_parts.append(chunk)


def extract_text(html: str) -> tuple[str | None, str | None, str]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.title_parts).split()) or None
    text = " ".join(parser.text_parts)[:TEXT_EXCERPT_CHARS]
    return title, parser.description, text


# ---------------------------------------------------------------- robots --
@dataclass
class _RobotsEntry:
    parser: robotparser.RobotFileParser | None  # None ⇒ no robots.txt (allow all)
    expires_at: float


@dataclass
class _RobotsCache:
    ttl: float = ROBOTS_TTL_SECONDS
    entries: dict[str, _RobotsEntry] = field(default_factory=dict)

    def get(self, host: str, *, now: float) -> _RobotsEntry | None:
        entry = self.entries.get(host)
        if entry is None or entry.expires_at <= now:
            return None
        return entry

    def put(self, host: str, parser: robotparser.RobotFileParser | None, *, now: float) -> None:
        self.entries[host] = _RobotsEntry(parser=parser, expires_at=now + self.ttl)


def _robots_allows(parser: robotparser.RobotFileParser | None, url: str) -> bool:
    if parser is None:
        return True
    # Both the wildcard group and our own group must allow the path.
    return parser.can_fetch("*", url) and parser.can_fetch(USER_AGENT, url)


# ----------------------------------------------------------------- fetcher --
class PublicPageFetcher:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        robots_cache: _RobotsCache | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http_client
        self._robots = robots_cache or _RobotsCache()
        self._clock = clock

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Language": "ar,en;q=0.8",
        }

    async def fetch(self, url: str, *, allowed_hosts: list[str]) -> PublicPage:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise PublicFetchRefused("host_not_allowed")
        host = normalize_host(parts.hostname)
        if host is None:
            raise PublicFetchRefused("host_not_allowed")
        if is_blocked_platform(host):  # before any network call
            raise PublicFetchRefused("blocked_platform", host=host)
        allowed = [normalize_host(h) or "" for h in allowed_hosts]
        if not any(a and host_matches(host, a) for a in allowed):
            raise PublicFetchRefused("host_not_allowed", host=host)
        if parts.username or parts.password:
            raise PublicFetchRefused("host_not_allowed", host=host)
        if parts.port not in (None, 80, 443):
            raise PublicFetchRefused("host_not_allowed", host=host)

        origin = f"{parts.scheme}://{parts.hostname}"
        robots = await self._robots_for(host, origin)
        if not _robots_allows(robots, url):
            raise PublicFetchRefused("robots_disallow", host=host)

        return await self._get_page(url, host)

    async def _robots_for(self, host: str, origin: str) -> robotparser.RobotFileParser | None:
        now = self._clock()
        cached = self._robots.get(host, now=now)
        if cached is not None:
            return cached.parser
        parser: robotparser.RobotFileParser | None = None
        try:
            response = await self._http.get(
                f"{origin}/robots.txt",
                headers=self._headers(),
                timeout=httpx.Timeout(TIMEOUT_SECONDS),
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise PublicFetchRefused("timeout", host=host) from exc
        except httpx.HTTPError:
            response = None  # unreachable robots ⇒ treat as absent (standard practice)
        if response is not None and response.status_code == 200:
            parser = robotparser.RobotFileParser()
            parser.parse(response.text[:ROBOTS_MAX_BYTES].splitlines())
        elif response is not None and response.status_code in (401, 403):
            # A robots.txt we are not allowed to read ⇒ assume everything is disallowed.
            parser = robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        self._robots.put(host, parser, now=now)
        return parser

    async def _get_page(self, url: str, host: str) -> PublicPage:
        received = 0
        chunks: list[bytes] = []
        try:
            async with self._http.stream(
                "GET",
                url,
                headers=self._headers(),
                timeout=httpx.Timeout(TIMEOUT_SECONDS),
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise PublicFetchRefused("non_html", host=host)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type.lower() not in ("text/html", "application/xhtml+xml"):
                    raise PublicFetchRefused("non_html", host=host)
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                    raise PublicFetchRefused("too_large", host=host)
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_BYTES:
                        raise PublicFetchRefused("too_large", host=host)  # stops the stream
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
        except httpx.TimeoutException as exc:
            raise PublicFetchRefused("timeout", host=host) from exc
        except httpx.HTTPError as exc:
            raise PublicFetchRefused("timeout", host=host) from exc
        html = b"".join(chunks).decode(encoding, errors="replace")
        title, description, text = extract_text(html)
        return PublicPage(
            host=host,
            status=status,
            title=title,
            meta_description=description,
            text_excerpt=text,
            fetched_at=datetime.now(tz=UTC),
        )
