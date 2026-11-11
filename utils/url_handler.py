"""Public URL fetching with layered SSRF protection.

Every hop is validated, DNS is resolved and checked before the request, and the
connection is pinned to the validated addresses so a DNS-rebinding response
cannot redirect the socket to a private address.  Automatic redirects are
disabled; redirects are followed manually and re-validated.
"""
from __future__ import annotations

import urllib.parse

import requests
from bs4 import BeautifulSoup

from config.settings import (
    MAX_REDIRECTS,
    MAX_URL_DOWNLOAD_SIZE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
)
from utils.security import (
    is_safe_url,
    pinned_dns,
    sanitize_text,
    validate_host_dns,
)

_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _port_for(parsed: urllib.parse.ParseResult) -> int:
    try:
        return int(parsed.port) if parsed.port else (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        return 443 if parsed.scheme == "https" else 80


def _validated_request(session: requests.Session, url: str, headers: dict):
    """Validate one URL, pin DNS, and perform a single non-redirecting GET."""
    safe, msg = is_safe_url(url, resolve_dns=False)
    if not safe:
        return None, f"Security Block: {msg}"

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    port = _port_for(parsed)

    dns_ok, dns_msg, addresses = validate_host_dns(hostname, port)
    if not dns_ok:
        return None, f"Security Block: {dns_msg}"

    # Pin the validated addresses for the lifetime of this request.  This is
    # the DNS-rebinding defence: the socket cannot resolve to a new address.
    with pinned_dns(hostname, addresses, port):
        response = session.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=False,
        )
    return response, ""


def fetch_url_content(url: str) -> tuple[bool, str, str]:
    """Safely fetch public webpage text, stripping unsafe HTML/scripts.

    Returns ``(success, content_or_error, title)``.
    """
    safe, msg = is_safe_url(url, resolve_dns=False)
    if not safe:
        return False, f"Security Block: {msg}", ""

    headers = {
        "User-Agent": "ProductSynthesisStudio/1.1 (Local Research Assistant; +https://localhost)"
    }

    session = requests.Session()
    current_url = url.strip()
    response = None
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            response, error = _validated_request(session, current_url, headers)
            if response is None:
                return False, error, ""

            if response.status_code in _REDIRECT_CODES or response.is_redirect:
                location = response.headers.get("Location")
                response.close()
                response = None
                if not location:
                    return False, "Security Block: redirect response had no destination.", ""

                next_url = urllib.parse.urljoin(current_url, location)
                next_safe, next_msg = is_safe_url(next_url, resolve_dns=False)
                if not next_safe:
                    return (
                        False,
                        f"Security Block: redirect destination '{next_url}' was blocked: {next_msg}",
                        "",
                    )
                current_url = next_url
                continue

            response.raise_for_status()
            break
        else:
            return False, f"Security Block: too many redirects (limit {MAX_REDIRECTS}).", ""

        if response is None:
            return False, "Security Block: no response received.", ""

        # Check content length
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_URL_DOWNLOAD_SIZE_BYTES:
                    return (
                        False,
                        f"Resource exceeded maximum download size ({MAX_URL_DOWNLOAD_SIZE_BYTES} bytes).",
                        "",
                    )
            except ValueError:
                pass

        # Check content type
        content_type = response.headers.get("Content-Type", "")
        if content_type and not any(t in content_type.lower() for t in _ALLOWED_CONTENT_TYPES):
            return False, f"Unsupported Content-Type: {content_type}", ""

        body_chunks = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > MAX_URL_DOWNLOAD_SIZE_BYTES:
                return False, "Download exceeded size limit during streaming.", ""
            body_chunks.append(chunk)

        raw_html = b"".join(body_chunks).decode(response.encoding or "utf-8", errors="replace")

        # Parse with BeautifulSoup to strip JS, styles, SVG, etc.
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "iframe"]):
            tag.decompose()

        page_title = soup.title.string.strip() if soup.title and soup.title.string else current_url
        text_content = soup.get_text(separator="\n", strip=True)
        sanitized = sanitize_text(text_content)

        if len(sanitized) < 50:
            return False, "Page extracted minimal readable text. May require manual copy/paste.", page_title

        return True, sanitized, page_title

    except requests.exceptions.Timeout:
        return False, f"Request timed out after {REQUEST_TIMEOUT_SECONDS} seconds.", ""
    except requests.exceptions.RequestException as e:
        return False, f"Network error during fetch: {str(e)}", ""
    except Exception as e:
        return False, f"Unexpected error during extraction: {str(e)}", ""
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        session.close()
