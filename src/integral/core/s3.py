"""
Anonymous read access to public S3 buckets laid out like the HEASARC archive tree.

NASA HEASARC mirrors the full INTEGRAL archive at s3://nasa-heasarc/integral/data/ (AWS Open
Data, us-east-1, no credentials needed), with the same layout as its HTTPS FTP area: scw/, aux/,
cat/, ic/, idx/. Objects are fetched with plain HTTPS GETs. Directory listings come from the S3
ListObjectsV2 API and are rendered as a minimal HTML index, so the downloader's existing
HTML-listing parsers work unchanged against S3. No boto3 or AWS CLI is needed.
"""

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

import httpx

from integral.core.http import get_with_retry

NASA_HEASARC_S3_BASE = "https://nasa-heasarc.s3.amazonaws.com/integral/data"

_S3_HOST = re.compile(r"^(?P<bucket>[a-z0-9][a-z0-9.-]+)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$")
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def is_s3_url(url: str) -> bool:
    """True for virtual-hosted-style S3 URLs (https://<bucket>.s3[.<region>].amazonaws.com/...)."""
    return bool(_S3_HOST.match(urlparse(url).hostname or ""))


def s3_uri_to_https(uri: str) -> str:
    """Convert s3://bucket/prefix to the bucket's virtual-hosted HTTPS URL."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not an s3:// URI: {uri!r}")
    return f"https://{parsed.netloc}.s3.amazonaws.com/{parsed.path.lstrip('/')}".rstrip("/")


def _split(url: str) -> tuple[str, str]:
    """Split an S3 URL into (bucket endpoint, key prefix)."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}", unquote(p.path.lstrip("/"))


async def s3_list(
    client: httpx.AsyncClient, dir_url: str, timeout: float = 30.0, max_keys: int = 1000
) -> httpx.Response:
    """List one 'directory' (prefix with delimiter '/'), following pagination.

    Returns a response with the S3 status code. On success its body is a minimal HTML
    index (`<a href="file">` for objects, `<a href="sub/">` for common prefixes), the shape
    the HEASARC directory-listing parsers expect."""
    endpoint, prefix = _split(dir_url)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    files: list[str] = []
    dirs: list[str] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "delimiter": "/", "max-keys": str(max_keys)}
        if token:
            params["continuation-token"] = token
        resp = await get_with_retry(client, f"{endpoint}/", params=params, timeout=timeout)
        if resp.status_code != 200:
            return resp
        root = ET.fromstring(resp.content)
        for c in root.iter(f"{_NS}Contents"):
            key = c.findtext(f"{_NS}Key") or ""
            name = key[len(prefix) :]
            if name and "/" not in name:
                files.append(name)
        for cp in root.iter(f"{_NS}CommonPrefixes"):
            sub = (cp.findtext(f"{_NS}Prefix") or "")[len(prefix) :]
            if sub:
                dirs.append(sub)
        if (root.findtext(f"{_NS}IsTruncated") or "").lower() != "true":
            break
        token = root.findtext(f"{_NS}NextContinuationToken")
        if not token:
            break

    # Names are used verbatim as local file names, so they are HTML-escaped but not URL-quoted.
    links = [f'<a href="{html.escape(d)}">{html.escape(d)}</a>' for d in dirs]
    links += [f'<a href="{html.escape(f)}">{html.escape(f)}</a>' for f in files]
    return httpx.Response(
        200,
        text="<html><body>\n" + "\n".join(links) + "\n</body></html>",
        request=httpx.Request("GET", dir_url),
    )
