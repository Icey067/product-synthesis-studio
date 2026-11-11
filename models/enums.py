from enum import Enum

class ExpertiseMode(str, Enum):
    EXPERT = "EXPERT"
    NON_EXPERT = "NON_EXPERT"

class ProductFormat(str, Enum):
    EBOOK = "Ebook"
    PRACTICAL_GUIDE = "Practical Guide"
    WORKBOOK = "Workbook"
    PLANNER = "Planner"
    CHECKLIST_BUNDLE = "Checklist Bundle"
    TEMPLATE_COLLECTION = "Template Collection"
    STRUCTURED_COURSE = "Structured Course"

class ReliabilityCategory(str, Enum):
    UNASSESSED = "UNASSESSED"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class SourceCategory(str, Enum):
    PDF = "PDF"
    TXT = "TXT"
    MARKDOWN = "MARKDOWN"
    CSV = "CSV"
    PUBLIC_URL = "PUBLIC_URL"
    MANUAL_NOTE = "MANUAL_NOTE"

class AuditStatus(str, Enum):
    PASS = "PASS"
    NEEDS_IMPROVEMENT = "NEEDS_IMPROVEMENT"
    REQUIRES_HUMAN_VERIFICATION = "REQUIRES_HUMAN_VERIFICATION"

class ExportFormat(str, Enum):
    PDF = "PDF"
    DOCX = "DOCX"
    MARKDOWN = "MARKDOWN"

