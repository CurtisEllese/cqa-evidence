import asyncio

import httpx
import pytest

from backend import media


class TrackStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def setup_proxy(monkeypatch, handler, addresses=None):
    requests = []
    clients = []
    dns_hosts = []

    async def dns(host):
        dns_hosts.append(host)
        return (addresses or {}).get(host, ["93.184.215.14"])

    def handle(request):
        requests.append(request)
        return handler(request)

    def factory():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False)
        clients.append(client)
        return client

    monkeypatch.setattr(media, "_dns_lookup", dns)
    monkeypatch.setattr(media, "_client_factory", factory)
    return requests, clients, dns_hosts


def response(body=b"audio", content_type="audio/mpeg", status=200, headers=None):
    stream = TrackStream([body])
    return httpx.Response(status, headers={"content-type": content_type, **(headers or {})}, stream=stream), stream


async def consume(result):
    return b"".join([chunk async for chunk in result.body_iterator])


def test_player_resolves_signed_source_pins_ip_and_preserves_range(monkeypatch):
    page, page_stream = response(b'<audio><source src="https://cdn.example/record.mp3?sig=SECRET&amp;key=1"></audio>', "text/html")
    audio, audio_stream = response(b"MP3", status=206, headers={"content-range": "bytes 2-4/50", "accept-ranges": "bytes", "content-length": "3", "location": "https://secret.invalid/?sig=SECRET"})
    def handler(request):
        if request.headers["host"] == "organizer.example":
            assert "range" not in request.headers
            return page
        assert request.headers["range"] == "bytes=2-4"
        assert request.url.query == b"sig=SECRET&key=1"
        return audio
    requests, clients, dns = setup_proxy(monkeypatch, handler)
    async def run():
        result = await media.audio_response("https://organizer.example/player?id=1", "organizer.example", "bytes=2-4")
        assert result.status_code == 206
        assert result.headers["content-range"] == "bytes 2-4/50"
        assert result.headers["content-length"] == "3"
        assert result.headers["accept-ranges"] == "bytes"
        assert result.headers["content-type"] == "audio/mpeg"
        assert "location" not in result.headers
        assert "SECRET" not in str(dict(result.headers))
        assert await consume(result) == b"MP3"
    asyncio.run(run())
    assert dns == ["organizer.example", "cdn.example"]
    assert all(request.url.host == "93.184.215.14" for request in requests)
    assert [request.extensions["sni_hostname"] for request in requests] == dns
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)
    assert page_stream.closed and audio_stream.closed and clients[0].is_closed


@pytest.mark.parametrize("url,trusted,expected", [
    ("https://organizer.example/player", False, 403),
    ("https://other.example/player", True, 403),
    ("http://organizer.example/player", True, 403),
    ("https://user:secret@organizer.example/player", True, 403),
    ("https://organizer.example:8443/player", True, 403),
    ("https://organizer.example\\@localhost/player", True, 403),
])
def test_untrusted_and_invalid_initial_urls_never_fetch(monkeypatch, url, trusted, expected):
    requests, _, dns = setup_proxy(monkeypatch, lambda _: pytest.fail("Unexpected network access"))
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response(url, "organizer.example", trusted=trusted))
    assert error.value.status_code == expected
    assert not requests and not dns


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.1.1", "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "2002:7f00:1::", "198.51.100.1"])
def test_nonpublic_dns_is_rejected_before_http(monkeypatch, address):
    requests, clients, _ = setup_proxy(monkeypatch, lambda _: pytest.fail("Unexpected network access"), {"organizer.example": [address]})
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 403
    assert not requests and clients[0].is_closed


def test_mixed_public_private_dns_rejected(monkeypatch):
    requests, _, _ = setup_proxy(monkeypatch, lambda _: pytest.fail("Unexpected network access"), {"organizer.example": ["93.184.215.14", "127.0.0.1"]})
    with pytest.raises(media.AudioProxyError):
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert not requests


@pytest.mark.parametrize("target", ["http://cdn.example/audio", "https://127.0.0.1/audio", "https://internal.example/audio", "file:///etc/passwd"])
def test_extracted_sources_are_validated(monkeypatch, target):
    page, page_stream = response(f'<audio src="{target}"></audio>'.encode(), "text/html")
    requests, clients, _ = setup_proxy(monkeypatch, lambda _: page, {"internal.example": ["10.0.0.4"]})
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 403
    assert len(requests) == 1 and page_stream.closed and clients[0].is_closed


def test_relative_audio_src_and_public_redirect(monkeypatch):
    streams = []
    def handler(request):
        if request.url.path == "/player":
            item, stream = response(b'<audio src="/media/one"></audio>', "text/html")
        elif request.url.path == "/media/one":
            item, stream = response(b"", status=302, headers={"location": "https://cdn.example/file.mp3"})
        else:
            item, stream = response(b"DATA")
        streams.append(stream)
        return item
    requests, clients, _ = setup_proxy(monkeypatch, handler)
    async def run():
        result = await media.audio_response("https://organizer.example/player", "organizer.example")
        assert await consume(result) == b"DATA"
    asyncio.run(run())
    assert len(requests) == 3 and all(stream.closed for stream in streams) and clients[0].is_closed


def test_redirect_to_private_host_rejected(monkeypatch):
    redirect, stream = response(b"", status=302, headers={"location": "https://169.254.169.254/metadata?secret=HIDDEN"})
    requests, clients, _ = setup_proxy(monkeypatch, lambda _: redirect)
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert len(requests) == 1 and stream.closed and clients[0].is_closed
    assert "HIDDEN" not in str(error.value)


def test_redirect_limit(monkeypatch):
    streams = []
    def handler(_):
        item, stream = response(b"", status=302, headers={"location": "/again"})
        streams.append(stream)
        return item
    requests, clients, _ = setup_proxy(monkeypatch, handler)
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 502
    assert len(requests) == 4 and all(stream.closed for stream in streams) and clients[0].is_closed


@pytest.mark.parametrize("body,headers", [(b"x" * (media.HTML_LIMIT + 1), {}), (b"small", {"content-length": str(media.HTML_LIMIT + 1)})])
def test_html_size_limit(monkeypatch, body, headers):
    page, stream = response(body, "text/html", headers=headers)
    _, clients, _ = setup_proxy(monkeypatch, lambda _: page)
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 502 and stream.closed and clients[0].is_closed


@pytest.mark.parametrize("byte_range", ["bytes=1-0", "bytes=-0", "bytes=0-1,4-5", "items=0-1", "bytes=abc-def", "bytes=0-1\r\nX-Test: hi"])
def test_invalid_ranges_do_not_fetch(monkeypatch, byte_range):
    requests, _, dns = setup_proxy(monkeypatch, lambda _: pytest.fail("Unexpected network access"))
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example", byte_range))
    assert error.value.status_code == 416 and not requests and not dns


def test_direct_audio_range_and_upstream_416(monkeypatch):
    streams = []
    def handler(request):
        if "range" not in request.headers:
            item, stream = response()
        else:
            assert request.headers["range"] == "bytes=1000-"
            item, stream = response(b"sensitive upstream error", "text/html", 416, {"content-range": "bytes */100", "accept-ranges": "bytes"})
        streams.append(stream)
        return item
    requests, clients, _ = setup_proxy(monkeypatch, handler)
    async def run():
        result = await media.audio_response("https://organizer.example/file.mp3", "organizer.example", "bytes=1000-")
        assert result.status_code == 416
        assert result.headers["content-range"] == "bytes */100"
        assert await consume(result) == b""
    asyncio.run(run())
    assert len(requests) == 2 and all(stream.closed for stream in streams) and clients[0].is_closed


def test_timeout_error_is_sanitized(monkeypatch):
    def handler(_):
        raise httpx.ReadTimeout("https://cdn.example?sig=SUPERSECRET")
    _, clients, _ = setup_proxy(monkeypatch, handler)
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 504 and "SUPERSECRET" not in str(error.value) and clients[0].is_closed


def test_stream_failure_closes_and_sanitizes(monkeypatch):
    stream = TrackStream([b"PART"], httpx.ReadError("https://cdn.example?sig=SUPERSECRET"))
    _, clients, _ = setup_proxy(monkeypatch, lambda _: httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=stream))
    async def run():
        result = await media.audio_response("https://organizer.example/file.mp3", "organizer.example")
        with pytest.raises(RuntimeError) as error:
            await consume(result)
        assert "SUPERSECRET" not in str(error.value)
    asyncio.run(run())
    assert stream.closed and clients[0].is_closed


def test_disconnect_before_body_closes_upstream(monkeypatch):
    audio, stream = response()
    _, clients, _ = setup_proxy(monkeypatch, lambda _: audio)
    async def run():
        result = await media.audio_response("https://organizer.example/file.mp3", "organizer.example")
        async def send(_):
            raise OSError("client disconnected")
        async def receive():
            return {"type": "http.disconnect"}
        with pytest.raises(Exception):
            await result({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    asyncio.run(run())
    assert stream.closed and clients[0].is_closed


@pytest.mark.parametrize("content_type,body,headers,status", [
    ("text/html", b"<html>No audio here</html>", {}, 200),
    ("text/html", b'<picture><source src="https://cdn.example/image.png"></picture>', {}, 200),
    ("application/json", b'{"signed_url":"SECRET"}', {}, 200),
    ("audio/mpeg", b"compressed", {"content-encoding": "gzip"}, 200),
    ("audio/mpeg", b"audio", {"content-range": "SECRET"}, 206),
    ("audio/mpeg", b"audio", {}, 206),
])
def test_invalid_upstream_content_rejected_and_closed(monkeypatch, content_type, body, headers, status):
    item, stream = response(body, content_type, status, headers)
    _, clients, _ = setup_proxy(monkeypatch, lambda _: item)
    with pytest.raises(media.AudioProxyError) as error:
        asyncio.run(media.audio_response("https://organizer.example/player", "organizer.example"))
    assert error.value.status_code == 502 and "SECRET" not in str(error.value)
    assert stream.closed and clients[0].is_closed
