"""Bounded organizer-player resolution and a private, range-aware audio proxy.

Call only with a URL read from the conversation database. ``trusted`` must be
true only for organizer DEV records, never for a URL supplied by the caller.
The configured organizer hostname restricts the initial player URL; extracted
media URLs and every redirect must resolve exclusively to public HTTPS hosts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import anyio
import httpx
from starlette.responses import StreamingResponse

HTML_LIMIT = 256 * 1024
TIMEOUT = 30.0
MAX_REDIRECTS = 3
_REDIRECTS = {301, 302, 303, 307, 308}
_RANGE = re.compile(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)\Z")
_CONTENT_RANGE = re.compile(r"bytes (?:[0-9]+-[0-9]+/(?:[0-9]+|\*)|\*/[0-9]+)\Z")
_AUDIO_TYPES = {"application/ogg", "application/octet-stream"}


class AudioProxyError(Exception):
    """A safe client-facing failure; no upstream URL or signed token is included."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _client_factory() -> httpx.AsyncClient:
    # Requests are pinned to IPs. Do not reuse a TLS connection for a different
    # logical hostname sharing that IP; never inherit local proxy credentials.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT),
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=4),
    )


async def _dns_lookup(host: str) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(record[4][0] for record in records))


def _public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id or ip.sixtofour or ip.teredo:
            return False
        if ip.ipv4_mapped:
            return _public_address(str(ip.ipv4_mapped))
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
    )


def _parse_url(value: str) -> httpx.URL:
    if not isinstance(value, str) or len(value) > 16384 or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise AudioProxyError(403, "Адрес аудиозаписи не разрешён.")
    try:
        raw = urlsplit(value)
        parsed = httpx.URL(value)
        if raw.scheme.lower() != "https" or not raw.hostname or "@" in raw.netloc or raw.port not in (None, 443):
            raise ValueError
        if not parsed.host or "%" in parsed.host:
            raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise AudioProxyError(403, "Адрес аудиозаписи не разрешён.") from None
    return parsed.copy_with(fragment=None)


async def _pinned_url(url: httpx.URL) -> tuple[httpx.URL, str]:
    host = url.host.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = await asyncio.wait_for(_dns_lookup(host), timeout=TIMEOUT)
    else:
        addresses = [str(literal)]
    if not addresses or not all(_public_address(address) for address in addresses):
        raise AudioProxyError(403, "Адрес аудиозаписи не разрешён.")
    # HTTPX's documented SNI extension preserves certificate verification for
    # the original host while the actual connection uses the validated IP.
    return url.copy_with(host=addresses[0]), host


class _Fetch:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.redirects = 0

    async def get(self, source: str, byte_range: str | None = None) -> tuple[httpx.Response, str]:
        current = source
        while True:
            logical = _parse_url(current)
            pinned, host = await _pinned_url(logical)
            headers = {"Host": f"[{host}]" if ":" in host else host, "Accept-Encoding": "identity", "Accept": "audio/*, text/html;q=0.9, */*;q=0.1"}
            if byte_range:
                headers["Range"] = byte_range
            # Pinned requests share an IP-shaped URL, so never carry cookies
            # from a prior logical host into the next request.
            self.client.cookies.clear()
            request = self.client.build_request(
                "GET", pinned, headers=headers, extensions={"sni_hostname": host}
            )
            response = await self.client.send(request, stream=True)
            if response.status_code not in _REDIRECTS:
                return response, str(logical)
            location = response.headers.get("location")
            await response.aclose()
            self.redirects += 1
            if not location or self.redirects > MAX_REDIRECTS:
                raise AudioProxyError(502, "Не удалось открыть аудиозапись.")
            current = urljoin(str(logical), location)


class _AudioSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.audio_depth = 0
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "audio":
            self.audio_depth += 1
        if tag == "audio" or (tag == "source" and self.audio_depth):
            source = values.get("src")
            if source and source.strip():
                self.sources.append(source.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "audio":
            self.audio_depth = max(0, self.audio_depth - 1)


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _is_audio(response: httpx.Response) -> bool:
    media_type = _content_type(response)
    return media_type.startswith("audio/") or media_type in _AUDIO_TYPES


def _check_encoding(response: httpx.Response) -> None:
    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise AudioProxyError(502, "Формат передачи аудиозаписи не поддерживается.")


async def _read_player(response: httpx.Response, source: str) -> str:
    _check_encoding(response)
    size = response.headers.get("content-length", "")
    if size.isdigit() and int(size) > HTML_LIMIT:
        raise AudioProxyError(502, "Страница аудиозаписи превышает допустимый размер.")
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(body) + len(chunk) > HTML_LIMIT:
            raise AudioProxyError(502, "Страница аудиозаписи превышает допустимый размер.")
        body.extend(chunk)
    parser = _AudioSources()
    try:
        text = body.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    parser.feed(text)
    if not parser.sources:
        raise AudioProxyError(502, "На странице не найдена аудиозапись.")
    return urljoin(source, parser.sources[0])


class _ManagedAudioResponse(StreamingResponse):
    def __init__(self, content, close, **kwargs):
        self._close_upstream = close
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send):
        # Also closes if the browser disconnects before body iteration begins.
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._close_upstream()


async def audio_response(
    url: str,
    organizer_host: str,
    range_header: str | None = None,
    trusted: bool = True,
) -> StreamingResponse:
    """Proxy DB-sourced organizer audio; map AudioProxyError to HTTPException.

    ``organizer_host`` is the hostname of ORGANIZER_DEV_URL, without a scheme.
    Set ``trusted=False`` for every manually imported conversation. That path
    rejects before DNS or HTTP access. Only a single HTTP byte range is accepted.
    """
    if not trusted:
        raise AudioProxyError(403, "Для импортированной записи доступно открытие исходной ссылки.")
    initial = _parse_url(url)
    if not organizer_host or initial.host.rstrip(".").lower() != organizer_host.rstrip(".").lower():
        raise AudioProxyError(403, "Источник аудиозаписи не разрешён.")
    if range_header:
        if len(range_header) > 100 or not _RANGE.fullmatch(range_header):
            raise AudioProxyError(416, "Некорректный диапазон аудиозаписи.")
        first, last = range_header[6:].split("-", 1)
        if (first and last and int(first) > int(last)) or (not first and int(last) == 0):
            raise AudioProxyError(416, "Некорректный диапазон аудиозаписи.")

    client = _client_factory()
    upstream: httpx.Response | None = None
    closed = False

    async def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        with anyio.CancelScope(shield=True):
            try:
                if upstream is not None:
                    try:
                        await upstream.aclose()
                    except (httpx.HTTPError, OSError):
                        pass
            finally:
                try:
                    await client.aclose()
                except (httpx.HTTPError, OSError):
                    pass

    try:
        fetch = _Fetch(client)
        # Resolve the entire player page without Range: slicing HTML would hide
        # its <source>. Direct audio is reused unless a range was requested.
        async with asyncio.timeout(TIMEOUT):
            upstream, final_url = await fetch.get(str(initial))
            if upstream.status_code not in (200, 206):
                raise AudioProxyError(502, "Аудиозапись временно недоступна.")
            if not _is_audio(upstream):
                if _content_type(upstream) not in ("text/html", "application/xhtml+xml"):
                    raise AudioProxyError(502, "Источник не содержит поддерживаемую аудиозапись.")
                media_url = await _read_player(upstream, final_url)
                await upstream.aclose()
                upstream, final_url = await fetch.get(media_url, range_header)
            elif range_header:
                await upstream.aclose()
                upstream, final_url = await fetch.get(final_url, range_header)

        if upstream.status_code == 416:
            headers = {"Cache-Control": "private, no-store"}
            content_range = upstream.headers.get("content-range", "")
            if re.fullmatch(r"bytes \*/[0-9]+", content_range):
                headers["Content-Range"] = content_range
            if "accept-ranges" in upstream.headers:
                headers["Accept-Ranges"] = upstream.headers["accept-ranges"]
            await close()
            return StreamingResponse(iter(()), status_code=416, headers=headers)
        if upstream.status_code not in (200, 206) or not _is_audio(upstream):
            raise AudioProxyError(502, "Аудиозапись временно недоступна.")
        _check_encoding(upstream)
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        for name in ("content-type", "content-length", "content-range", "accept-ranges"):
            value = upstream.headers.get(name)
            if value is not None:
                if name == "content-length" and not value.isdigit():
                    raise AudioProxyError(502, "Некорректный ответ источника аудиозаписи.")
                if name == "content-range" and not _CONTENT_RANGE.fullmatch(value):
                    raise AudioProxyError(502, "Некорректный ответ источника аудиозаписи.")
                headers[name] = value
        if upstream.status_code == 206 and "content-range" not in headers:
            raise AudioProxyError(502, "Некорректный ответ источника аудиозаписи.")

        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except (httpx.HTTPError, OSError):
                # Headers have already been sent; terminate the stream without
                # forwarding/logging an exception containing a signed URL.
                raise RuntimeError("Передача аудиозаписи прервана.") from None
            finally:
                await close()

        return _ManagedAudioResponse(stream(), close, status_code=upstream.status_code, headers=headers)
    except AudioProxyError:
        await close()
        raise
    except (httpx.TimeoutException, TimeoutError):
        await close()
        raise AudioProxyError(504, "Источник аудиозаписи не ответил вовремя.") from None
    except (httpx.HTTPError, OSError, ValueError):
        await close()
        raise AudioProxyError(502, "Не удалось загрузить аудиозапись.") from None
    except BaseException:
        await close()
        raise
