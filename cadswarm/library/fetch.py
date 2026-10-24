"""Downloading a part file from a direct link (an agent or a person found it online).

The host fetches it, so the rules are strict: https only, no addresses on the host's own network
(loopback, private, link-local), at most MAX_BYTES, and only part formats (.step/.stp/.FCStd/.stl,
or a .zip pack). Nothing is scraped: give the link to the file itself. GitHub "blob" page links are
turned into their raw-file links.
"""
import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.request

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT = 30
ALLOWED_EXT = {".step", ".stp", ".fcstd", ".stl", ".zip"}
USER_AGENT = "CadSwarm-library/1 (+https://github.com/SachchitBalamurugan/cadswarm)"


class FetchError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize(url):
    url = str(url or "").strip()
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    if m:
        url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


def _public(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise FetchError("fetch_failed", f"Couldn't find {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("not_allowed", f"{host} is on a private or local network; the host only downloads "
                                            "from the public internet.")


def filename_from(url, headers):
    cd = headers.get("Content-Disposition", "") if headers else ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    name = urllib.parse.unquote(m.group(1)) if m else urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    return os.path.basename(name.strip()) or "part"


def check_ext(name):
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise FetchError("bad_type", f"{name!r} isn't a part file. Link straight to a .step, .stp, .FCStd, .stl "
                                     "(or a CadSwarm .zip pack), not to a web page about it.")
    return ext


def fetch(url, opener=None, allow_private=False, max_bytes=MAX_BYTES):
    """(filename, bytes, final_url). `opener`/`allow_private` exist for tests."""
    url = normalize(url)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise FetchError("bad_args", "Use an https:// link to the file.")
    if not allow_private:
        _public(parsed.hostname)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=TIMEOUT) as resp:
            final = resp.geturl() if hasattr(resp, "geturl") else url
            fparsed = urllib.parse.urlparse(final)
            if fparsed.scheme != "https":
                raise FetchError("not_allowed", "The link redirected away from https.")
            if not allow_private and fparsed.hostname != parsed.hostname:
                _public(fparsed.hostname)
            name = filename_from(final, resp.headers)
            if not os.path.splitext(name)[1]:
                name = filename_from(url, resp.headers)
            check_ext(name)
            length = resp.headers.get("Content-Length") if resp.headers else None
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too_big", f"That file is {int(length) // (1024 * 1024)} MB; the limit is "
                                            f"{max_bytes // (1024 * 1024)} MB.")
            chunks, total = [], 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("too_big", f"That file is over the {max_bytes // (1024 * 1024)} MB limit.")
                chunks.append(chunk)
    except FetchError:
        raise
    except Exception as e:  # HTTP errors, timeouts, TLS failures: say what happened, keep the helper alive
        raise FetchError("fetch_failed", f"Couldn't download {url}: {e}")
    data = b"".join(chunks)
    if not data:
        raise FetchError("fetch_failed", "The download was empty.")
    return name, data, final
