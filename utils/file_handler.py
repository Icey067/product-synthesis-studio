import hashlib
from pathlib import Path
from typing import Tuple
import fitz  # PyMuPDF
from config.settings import MAX_UPLOAD_SIZE_MB, ALLOWED_UPLOAD_EXTENSIONS
from utils.security import sanitize_text

def compute_hash(data: bytes | str) -> str:
    """Generate SHA-256 hash for deduplication."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def extract_text_from_file(file_name: str, file_bytes: bytes) -> Tuple[bool, str, str]:
    """
    Extracts plain text from uploaded PDF, TXT, MD, or CSV bytes.
    Returns: (success: bool, text_content: str, error_msg: str)
    """
    # Size check
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        return False, "", f"File size ({size_mb:.2f} MB) exceeds maximum allowed limit of {MAX_UPLOAD_SIZE_MB} MB."

    ext = Path(file_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return False, "", f"File extension '{ext}' is not permitted. Allowed: {', '.join(ALLOWED_UPLOAD_EXTENSIONS)}"

    try:
        if ext == ".pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            extracted_pages = []
            for page in doc:
                text = page.get_text("text")
                if text:
                    extracted_pages.append(text)
            doc.close()
            full_text = "\n\n".join(extracted_pages)
            return True, sanitize_text(full_text), ""

        elif ext in (".txt", ".md", ".csv"):
            try:
                text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = file_bytes.decode("latin-1", errors="replace")
            return True, sanitize_text(text), ""

        else:
            return False, "", f"Unsupported file type: {ext}"

    except Exception as e:
        return False, "", f"Failed to extract document contents: {str(e)}"

