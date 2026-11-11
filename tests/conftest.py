"""Shared pytest configuration.

Every test session uses an isolated temporary SQLite database and output
directory so test projects never pollute the real ``database/pss_data.db`` or
``output/`` folder.  Paths are assigned unconditionally (not via
``setdefault``) so an inherited production environment variable can never
redirect a test run at the live database.

A session guard also fingerprints the production database before and after the
suite and fails the session if it changed.
"""
import hashlib
import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DB = PROJECT_ROOT / "database" / "pss_data.db"
PRODUCTION_OUTPUT = PROJECT_ROOT / "output"
PRODUCTION_BACKUPS = PROJECT_ROOT / "database" / "backups"

_SESSION_TMP = Path(tempfile.mkdtemp(prefix="pss_test_"))

# Unconditional isolation: overwrite anything inherited from the shell.
os.environ["PSS_DATABASE_PATH"] = str(_SESSION_TMP / "pss_test.db")
os.environ["PSS_OUTPUT_DIR"] = str(_SESSION_TMP / "output")
os.environ["PSS_BACKUP_DIR"] = str(_SESSION_TMP / "backups")

import pytest  # noqa: E402

# Import after the environment is fixed so config.settings resolves to the
# temporary paths.
import config.settings as app_settings  # noqa: E402
from database.db import init_db  # noqa: E402


def _fingerprint(path: Path) -> tuple:
    """Cheap change fingerprint for a file or directory."""
    if not path.exists():
        return ("missing",)
    if path.is_file():
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ("file", stat.st_size, stat.st_mtime_ns, digest)
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            stat = item.stat()
            entries.append((str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns))
    return ("dir", tuple(entries))


def _assert_isolated_paths() -> None:
    tmp = str(_SESSION_TMP)
    assert tmp in str(app_settings.DATABASE_PATH), (
        f"Test database escaped the temporary directory: {app_settings.DATABASE_PATH}"
    )
    assert tmp in str(app_settings.OUTPUT_DIR), (
        f"Test output escaped the temporary directory: {app_settings.OUTPUT_DIR}"
    )
    assert tmp in str(app_settings.BACKUP_DIR), (
        f"Test backup directory escaped the temporary directory: {app_settings.BACKUP_DIR}"
    )


_PROTECTED = {
    "db": _fingerprint(PRODUCTION_DB),
    "output": _fingerprint(PRODUCTION_OUTPUT),
    "backups": _fingerprint(PRODUCTION_BACKUPS),
}


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    _assert_isolated_paths()
    init_db()
    yield
    # Never let a test run mutate real user data.
    assert _fingerprint(PRODUCTION_DB) == _PROTECTED["db"], (
        "The production database changed during the test session. "
        "A test bypassed the temporary database."
    )
    assert _fingerprint(PRODUCTION_OUTPUT) == _PROTECTED["output"], (
        "The production output directory changed during the test session."
    )
    assert _fingerprint(PRODUCTION_BACKUPS) == _PROTECTED["backups"], (
        "The production backup directory changed during the test session."
    )


@pytest.fixture(autouse=True)
def _ensure_schema():
    """Keep schema/migrations applied even if a test creates a fresh file."""
    _assert_isolated_paths()
    init_db()
    yield
