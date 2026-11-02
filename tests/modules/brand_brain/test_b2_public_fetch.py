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
    ROBOTS_MAX_BYTES,
    USER_AGENT,
    PublicFetchRefused,
    PublicPageFetcher,
    _RobotsCache,
    extract_text,
)

pytestmark = pytest.mark.unit

ALLOWED = ["beanery.com"]
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def _addrinfo(*addresses: str) -> list[tuple[object, ...]]:
    """``getaddrinfo``-shaped rows for the given literal addresses (no network)."""
    import socket

    rows: list[tuple[object, ...]] = []
    for addr in addresses:
        if ":" in addr:
            rows.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, 443, 0, 0)))
        else:
            rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 443)))
    return rows


def _resolver(*addresses: str, fail: Exception | None = None):  # noqa: ANN202
    calls: list[tuple[str, int]] = []

    async def resolve(host: str, port: int) -> list[tuple[object, ...]]:
        calls.append((host, port))
        if fail is not None:
            raise fail
        return _addrinfo(*addresses)

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


HTML = (
    "<html><head><title> Beanery  Coffee </title>"
    '<meta name="description" content="Slow coffee, fast smiles">'
    "<script>var x = 'nope';</script><style>.a{}</style></head>"
    "<body><h1>Beanery</h1><p>Cold   brew &amp; pastries</p><noscript>no</noscript></body></html>"
)


def _fetcher(client: httpx.AsyncClient, resolver=None) -> PublicPageFetcher:  # noqa: ANN001
    # A-2: never touch real DNS in unit tests — resolve to a public address by default.
    return PublicPageFetcher(
        client, robots_cache=_RobotsCache(), resolver=resolver or _resolver(PUBLIC_V4)
    )


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


# ------------------------------------------------------- SPRINT-11 A-2 (SSRF) --
@pytest.mark.parametrize(
    ("host", "address"),
    [
        ("127.0.0.1.nip.io", "127.0.0.1"),  # loopback
        ("169.254.169.254.nip.io", "169.254.169.254"),  # link-local / cloud metadata
        ("10.0.0.1.nip.io", "10.0.0.1"),  # RFC 1918
        ("fd00--1.sslip.io", "fd00::1"),  # IPv6 ULA
        ("0.0.0.0.nip.io", "0.0.0.0"),  # unspecified  # noqa: S104
        ("224.0.0.1.nip.io", "224.0.0.1"),  # multicast (ipaddress says is_global=True!)
        ("ff02--1.sslip.io", "ff02::1"),  # IPv6 multicast
        ("64-ff9b--a00-1.sslip.io", "64:ff9b::a00:1"),  # NAT64-wrapped 10.0.0.1
        ("192.168.1.9.nip.io", "192.168.1.9"),
        ("0--1.sslip.io", "::1"),  # IPv6 loopback
        ("mapped.example", "::ffff:10.0.0.1"),  # IPv4-mapped private
        ("100.64.0.1.nip.io", "100.64.0.1"),  # CGNAT
    ],
)
async def test_a2_private_address_refused_with_zero_network_calls(host: str, address: str) -> None:
    """A-2 — the host resolves to a non-global address ⇒ ``private_address`` **before** robots
    and before the page: ``respx.calls.call_count == 0``."""
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client, _resolver(address)).fetch(
                    f"https://{host}/", allowed_hosts=[host]
                )
        assert router.calls.call_count == 0
    assert info.value.reason == "private_address"
    assert info.value.host == host


async def test_a2_mixed_public_and_private_addresses_is_refused() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client, _resolver(PUBLIC_V4, "10.0.0.1")).fetch(
                    "https://beanery.com/", allowed_hosts=ALLOWED
                )
        assert router.calls.call_count == 0
    assert info.value.reason == "private_address"


async def test_a2_public_ipv6_passes_and_resolver_sees_host_and_port() -> None:
    resolver = _resolver(PUBLIC_V6)
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(
            return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            page = await _fetcher(client, resolver).fetch(
                "https://beanery.com/", allowed_hosts=ALLOWED
            )
    assert page.title == "Beanery Coffee"
    assert resolver.calls == [("beanery.com", 443)]  # resolved once, before robots + page


@pytest.mark.parametrize("failure", [OSError("NXDOMAIN"), ValueError("bad host")])
async def test_a2_unresolvable_host_is_host_not_allowed_with_zero_calls(
    failure: Exception,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client, _resolver(fail=failure)).fetch(
                    "https://beanery.com/", allowed_hosts=ALLOWED
                )
        assert router.calls.call_count == 0
    assert info.value.reason == "host_not_allowed"


async def test_a2_empty_resolution_is_host_not_allowed() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client, _resolver()).fetch(
                    "https://beanery.com/", allowed_hosts=ALLOWED
                )
        assert router.calls.call_count == 0
    assert info.value.reason == "host_not_allowed"


def test_a2_default_resolver_is_getaddrinfo_and_scope_ids_are_handled() -> None:
    import asyncio

    from src.modules.brand_brain.public_fetch import _is_global_address, default_resolver

    assert _is_global_address(("fe80::1%eth0", 443, 0, 0)) is False
    assert _is_global_address(("not-an-ip", 443)) is False
    assert _is_global_address((PUBLIC_V4, 443)) is True
    infos = asyncio.run(default_resolver("localhost", 443))
    assert infos and all(not _is_global_address(i[4]) for i in infos)


# -------------------------------------------------- SPRINT-11 A-3 (robots stream) --
async def test_a3_huge_robots_is_read_at_most_64kib_and_not_too_large() -> None:
    """A-3 — a 4.7 MiB robots.txt: the fetcher stops after ``ROBOTS_MAX_BYTES`` (counted on
    the byte generator), never raises ``too_large``, and the page is still fetched."""
    chunk = b"# padding line\n" * 4096  # 60 KiB per chunk
    total = (4700 * 1024) // len(chunk) + 1
    served = {"bytes": 0}

    async def body():  # noqa: ANN202
        for _ in range(total):
            served["bytes"] += len(chunk)
            yield chunk

    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(
            return_value=httpx.Response(200, stream=_AsyncStream(body))  # type: ignore[arg-type]
        )
        router.get("/").mock(
            return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            page = await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert page.title == "Beanery Coffee"
    assert served["bytes"] <= ROBOTS_MAX_BYTES + len(chunk)
    assert served["bytes"] < 4700 * 1024 // 10


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, gen) -> None:  # noqa: ANN001
        self._gen = gen

    async def __aiter__(self):  # noqa: ANN204
        async for part in self._gen():
            yield part


async def test_a3_robots_disallow_within_first_64kib_is_honoured() -> None:
    text = "User-agent: *\nDisallow: /\n" + "# pad\n" * 20000  # > 64 KiB total
    with respx.mock(base_url="https://beanery.com", assert_all_called=False) as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(200, text=text))
        page = router.get("/").mock(return_value=httpx.Response(200, text=HTML))
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert info.value.reason == "robots_disallow"
    assert page.call_count == 0


@pytest.mark.parametrize("status", [404, 410, 500, 503, 301])
async def test_a3_non_200_robots_other_than_401_403_means_no_robots(status: int) -> None:
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(status))
        router.get("/").mock(
            return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            page = await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert page.title == "Beanery Coffee"


# ------------------------------------------------ SPRINT-11 A-4 (error pages) --
@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
async def test_a4_error_page_is_unavailable_with_status(status: int) -> None:
    with respx.mock(base_url="https://beanery.com") as router:
        router.get("/robots.txt").mock(return_value=httpx.Response(404))
        router.get("/").mock(
            return_value=httpx.Response(
                status, text="<html>maintenance</html>", headers={"content-type": "text/html"}
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(PublicFetchRefused) as info:
                await _fetcher(client).fetch("https://beanery.com/", allowed_hosts=ALLOWED)
    assert info.value.reason == "unavailable"
    assert info.value.status == status
    assert info.value.host == "beanery.com"


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
