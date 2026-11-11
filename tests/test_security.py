import pytest
from utils.security import is_safe_url, sanitize_text
from utils.file_handler import extract_text_from_file

def test_ssrf_blocking_localhost():
    bad_urls = [
        "http://localhost:11434",
        "http://127.0.0.1:8000",
        "http://192.168.1.1/admin",
        "http://10.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/data",
    ]
    for url in bad_urls:
        safe, msg = is_safe_url(url)
        assert safe is False, f"URL {url} should have been blocked"

def test_safe_public_url():
    safe_urls = [
        "https://www.google.com",
        "https://en.wikipedia.org/wiki/Retail",
        "http://example.com/research",
    ]
    for url in safe_urls:
        safe, _ = is_safe_url(url)
        assert safe is True, f"URL {url} should be permitted"

def test_disallowed_file_types():
    fake_exe = b"MZ\x90\x00"
    ok, text, msg = extract_text_from_file("virus.exe", fake_exe)
    assert ok is False
    assert "not permitted" in msg

def test_oversized_file_rejected():
    huge_data = b"0" * (26 * 1024 * 1024)  # 26MB > 25MB limit
    ok, text, msg = extract_text_from_file("huge.txt", huge_data)
    assert ok is False
    assert "exceeds maximum allowed limit" in msg

