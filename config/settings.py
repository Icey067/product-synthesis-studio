import os
from pathlib import Path

# Base Paths (environment overrides allow isolated test databases and outputs)
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("PSS_OUTPUT_DIR", str(BASE_DIR / "output")))
ASSETS_DIR = BASE_DIR / "assets"
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
DATABASE_PATH = Path(os.getenv("PSS_DATABASE_PATH", str(BASE_DIR / "database" / "pss_data.db")))
BACKUP_DIR = Path(os.getenv("PSS_BACKUP_DIR", str(BASE_DIR / "database" / "backups")))

# Ensure directories exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Application metadata
APP_NAME = "PRODUCT SYNTHESIS STUDIO"
APP_TAGLINE = "Local-First AI Digital Product Creation Factory"
APP_VERSION = "1.1.0"

# Ollama Defaults
#
# There is deliberately no DEFAULT_MODEL constant. Models are discovered from
# the local Ollama daemon at runtime (see ``services.model_registry``) so the
# app can never send a request for a model that is not installed.
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_CONTEXT_WINDOW = 8192
DEFAULT_TEMPERATURE = 0.7
DEFAULT_KEEP_ALIVE = "5m"
DEFAULT_TIMEOUT_SECONDS = 180

# Generation contracts
DEFAULT_CHAPTER_TARGET_WORDS = 2200
DEFAULT_MIN_CHAPTER_WORDS = 1000
DEFAULT_REQUIRED_CHAPTER_COUNT = 11

# Security / Limits
MAX_UPLOAD_SIZE_MB = 25
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}
REQUEST_TIMEOUT_SECONDS = 15
MAX_URL_DOWNLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_REDIRECTS = 5
BLOCKED_IP_NETWORKS = [
    "0.0.0.0/8",
    "127.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",      # carrier-grade NAT
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "198.18.0.0/15",       # benchmarking
    "240.0.0.0/4",         # reserved
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "::ffff:127.0.0.0/104",
]
