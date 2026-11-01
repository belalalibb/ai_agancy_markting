"""SPRINT-10 B-2 — ``public_fetch.py`` (D-074 ج): refusals happen **before** any network
call where the rule is static (blocked platform / host not allowed), ``robots.txt`` is read
first and honoured, the body is capped at 512 KiB, only ``text/html`` is parsed, redirects are
never followed, and the stdlib parser extracts title / description / text.

Every test runs under ``respx`` with ``assert_all_called=False`` so **0 calls** can be asserted
literally (acceptance §3-4 / §3-5)."""

from __future__ import annotations

import httpx
import pytest
import respx
from src.modules.brand_brain.domain.hosts import BLOCKED_PLATFORM_HOSTS
from src.modules.brand_brain.public_fetch import (
    MAX_BYTES,
    USER_AGENT,
    PublicFetchRefused,
    PublicPageFetcher,
    _RobotsCache,
    extract_text,
)

pytestmark = pytest.mark.unit

ALLOWED = ["beanery.com"]
HTML = (
    "<html><head><title> Beanery  Coffee </title>"
    '<meta name="description" content="Slow coffee, fast smiles">'
    "<script>var x = 'nope';</script><style>.a{}</style></head>"
    "<body><h1>Beanery</h1><p>Cold   brew &amp; pastries</p><noscript>no</noscript></body></html>"
)


def _fetcher(client: httpx.AsyncClient) -> PublicPageFetcher:
    return PublicPageFetcher(client, robots_cache=_RobotsCache())


@pytest.mark.parametrize(
    "host", sorted(BLOCKED_PLATFORM_HOSTS) + ["www.instagram.com", "m.tiktok.com"]
)
async def test_blocked_platform_refused_before_any_network_call(host: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                # Even when the owner (wrongly) allow-listed it, the block list wins.
                await _fetcher(client).fetch(f"https://{host}/page", allowed_hosts=[host])
    assert info.value.reason == "blocked_platform"
    assert route.call_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",  # not in allowed_hosts
        "https://beanery.com.evil.example/",  # suffix trick
        "ftp://beanery.com/",  # scheme
        "https://user:pw@beanery.com/",  # credentials
        "https://beanery.com:8443/",  # non-standard port
        "not a url",
    ],
)
async def test_host_not_allowed_refused_with_zero_calls(url: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch(url, allowed_hosts=ALLOWED)
    assert info.value.reason == "host_not_allowed"
    assert route.call_count == 0


async def test_robots_disallow_refuses_and_never_fetches_the_page() -> None:
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        robots = router.get("/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        )
        page = router.get("/menu").mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            fetcher = _fetcher(client)
            with pytest.raises(PublicFetchRefused) as info:
                await fetcher.fetch("https://beanery.com/menu", allowed_hosts=ALLOWED)
            # Cached for an hour: a second attempt re-reads nothing.
            with pytest.raises(PublicFetchRefused):
                await fetcher.fetch("https://beanery.com/other", allowed_hosts=ALLOWED)
    assert info.value.reason == "robots_disallow"
    assert robots.call_count == 1
    assert page.call_count == 0
    assert robots.calls[0].request.headers["User-Agent"] == USER_AGENT


async def test_robots_disallow_for_our_agent_only_is_honoured() -> None:
    body = "User-agent: *\nAllow: /\n\nUser-agent: AIGrowthOS-BrandBrain\nDisallow: /\n"
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(200, text=body))
        page = router.get("/").mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert info.value.reason == "robots_disallow"
    assert page.call_count == 0


@pytest.mark.parametrize("status", [401, 403])
async def test_unreadable_robots_means_disallow(status: int) -> None:
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(status))
        page = router.get("/").mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert info.value.reason == "robots_disallow"
    assert page.call_count == 0


async def test_missing_robots_allows_and_page_is_parsed_with_stdlib() -> None:
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        page = router.get("/").mock(
            return_value=httpx.Response(
                200, text=HTML, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert result.host == "beanery.com"
    assert result.status == 200
    assert result.title == "Beanery Coffee"
    assert result.meta_description == "Slow coffee, fast smiles"
    assert result.text_excerpt == "Beanery Cold brew & pastries"
    assert "nope" not in result.text_excerpt
    request = page.calls[0].request
    assert request.method == "GET"
    assert request.headers["User-Agent"] == USER_AGENT
    assert "cookie" not in {k.lower() for k in request.headers}


async def test_subdomain_of_allowed_host_is_allowed() -> None:
    with respx.mock(base_url="https://shop.beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(
            return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            result = await _fetcher(client).fetch(
                "https://shop.beanery.com/", allowed_hosts=ALLOWED
            )
    assert result.host == "shop.beanery.com"


async def test_too_large_declared_and_streamed() -> None:
    big = b"<html><body>" + b"x" * (MAX_BYTES + 1) + b"</body></html>"
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/declared").mock(
            return_value=httpx.Response(
                200,
                content=b"<html></html>",
                headers={"content-type": "text/html", "content-length": str(MAX_BYTES + 1)},
            )
        )
        router.get("/streamed").mock(
            return_value=httpx.Response(200, content=big, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            fetcher = _fetcher(client)
            with pytest.raises(PublicFetchRefused) as declared:
                await fetcher.fetch("https://beanery.com/declared", allowed_hosts=ALLOWED)
            with pytest.raises(PublicFetchRefused) as streamed:
                await fetcher.fetch("https://beanery.com/streamed", allowed_hosts=ALLOWED)
    assert declared.value.reason == "too_large"
    assert streamed.value.reason == "too_large"


async def test_exactly_max_bytes_is_accepted() -> None:
    prefix, suffix = b"<html><body>", b"</body></html>"
    body = prefix + b"y" * (MAX_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(body) == MAX_BYTES
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(
            return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            result = await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert len(result.text_excerpt) == 4000


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (200, {"content-type": "application/json"}),
        (200, {"content-type": "application/pdf"}),
        (301, {"location": "https://other.example/", "content-type": "text/html"}),
        (302, {"location": "https://beanery.com/x", "content-type": "text/html"}),
    ],
)
async def test_non_html_and_redirects_are_refused(status: int, headers: dict[str, str]) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://beanery.com/robots.txt").mock(return_value=httpx.Response(404))
        router.get("https://beanery.com/").mock(
            return_value=httpx.Response(status, content=b"{}", headers=headers)
        )
        other = router.get("https://other.example/").mock(
            return_value=httpx.Response(200, text=HTML)
        )
        follow = router.get("https://beanery.com/x").mock(
            return_value=httpx.Response(200, text=HTML)
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert info.value.reason == "non_html"
    assert other.call_count == 0 and follow.call_count == 0


async def test_timeout_on_robots_and_on_page() -> None:
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(side_effect=httpx.ReadTimeout("slow"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
        assert info.value.reason == "timeout"
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(side_effect=httpx.ConnectTimeout("slow"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
        assert info.value.reason == "timeout"
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
        assert info.value.reason == "timeout"


async def test_unreachable_robots_is_treated_as_absent() -> None:
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
        router.get("/").mock(
            return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            result = await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert result.title == "Beanery Coffee"


def test_robots_cache_expires_after_ttl() -> None:
    cache = _RobotsCache(ttl=10)
    cache.put("beanery.com", None, now=100.0)
    assert cache.get("beanery.com", now=105.0) is not None
    assert cache.get("beanery.com", now=110.0) is None
    assert cache.get("unknown.example", now=0.0) is None


def test_extract_text_handles_missing_parts_and_nested_skips() -> None:
    title, description, text = extract_text(
        "<p>plain</p><svg><text>icon</text></svg><template>t</template>"
    )
    assert title is None and description is None
    assert text == "plain"
    title, _, _ = extract_text("<title></title>")
    assert title is None


def test_block_list_is_exactly_the_directive_list() -> None:
    assert BLOCKED_PLATFORM_HOSTS == frozenset(
        {
            "instagram.com",
            "facebook.com",
            "tiktok.com",
            "whatsapp.com",
            "linkedin.com",
            "x.com",
            "twitter.com",
            "snapchat.com",
            "youtube.com",
        }
    )
