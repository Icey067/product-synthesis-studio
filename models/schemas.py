from typing import List, Optional, Dict, Any
import json
from pydantic import BaseModel, Field, field_validator
from models.enums import ExpertiseMode, ProductFormat, ReliabilityCategory, AuditStatus
from config.settings import (
    DEFAULT_CHAPTER_TARGET_WORDS,
    DEFAULT_MIN_CHAPTER_WORDS,
    DEFAULT_REQUIRED_CHAPTER_COUNT,
)

# --- Project Schemas ---
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    country: str = "United States"
    language: str = "English"
    target_customer: Optional[str] = None
    expertise_mode: ExpertiseMode = ExpertiseMode.NON_EXPERT
    product_category: str = "Business & Productivity"
    product_format: ProductFormat = ProductFormat.EBOOK
    budget: Optional[float] = 0.0
    creator_interests: Optional[str] = None
    required_chapter_count: int = Field(default=DEFAULT_REQUIRED_CHAPTER_COUNT, ge=1, le=40)
    target_chapter_words: int = Field(default=DEFAULT_CHAPTER_TARGET_WORDS, ge=200, le=10000)
    min_chapter_words: int = Field(default=DEFAULT_MIN_CHAPTER_WORDS, ge=100, le=10000)

    @field_validator("min_chapter_words")
    @classmethod
    def _min_not_above_target(cls, value, info):
        target = info.data.get("target_chapter_words")
        if target is not None and value > target:
            raise ValueError("min_chapter_words cannot exceed target_chapter_words")
        return value

class Project(ProjectCreate):
    id: int
    created_at: str
    updated_at: str
    status: str = "DRAFT"
    research_progress: int = 0
    selected_opportunity_id: Optional[int] = None

# --- Market & Opportunity Schemas ---
class OpportunityEvidenceItem(BaseModel):
    """A single claim linked to a real research source.

    The model must supply the source ID and an excerpt that actually exists in
    that source. The application re-verifies both before accepting the claim.
    """
    field: str = "demand_evidence"
    source_id: int
    excerpt: str = ""
    claim: str = ""


class OpportunityItem(BaseModel):
    name: str
    exact_customer: str
    customer_problem: str
    problem_urgency: str
    desired_outcome: str
    demand_evidence: str = "Unknown"
    willingness_to_pay_evidence: str = "Unknown"
    existing_competitors: str = "None observed"
    competitor_products: str = "None observed"
    observed_prices: str = "Unknown"
    customer_complaints: str = "Unknown"
    missing_features: str = "Unknown"
    potential_solution: str
    commercial_risks: str = "Market competition"
    ethical_considerations: str = "Ensure disclaimers on outcomes"
    evidence_confidence: str = "HYPOTHESIS"
    evidence: List[OpportunityEvidenceItem] = Field(default_factory=list)
    demand_score: float = Field(default=5.0, ge=1, le=10)
    willingness_to_pay_score: float = Field(default=5.0, ge=1, le=10)
    urgency_score: float = Field(default=5.0, ge=1, le=10)
    competitor_gap_score: float = Field(default=5.0, ge=1, le=10)
    customer_specificity_score: float = Field(default=5.0, ge=1, le=10)
    practical_solvability_score: float = Field(default=5.0, ge=1, le=10)
    feasibility_score: float = Field(default=5.0, ge=1, le=10)
    scoring_reasoning: str = ""
    missing_evidence: str = ""

    @field_validator(
        "demand_evidence",
        "willingness_to_pay_evidence",
        "existing_competitors",
        "competitor_products",
        "observed_prices",
        "customer_complaints",
        "missing_features",
        "potential_solution",
        "commercial_risks",
        "ethical_considerations",
        mode="before",
    )
    @classmethod
    def _coerce_evidence_text(cls, value):
        """Local models sometimes return a claim object instead of a string.

        Coerce it into readable text instead of failing the whole discovery run.
        The excerpt itself is still verified separately against the source.
        """
        if value is None:
            return "Unknown"
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("claim", "text", "value", "summary", "finding", "excerpt"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (list, tuple)):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(cls._coerce_evidence_text(item))
                else:
                    parts.append(str(item))
            return "; ".join(p for p in parts if p)
        return str(value)

class OpportunityResponse(BaseModel):
    opportunities: List[OpportunityItem]

class OpportunityScoreDetail(BaseModel):
    weighted_score: float  # 0 to 100 -- model-estimated opportunity score
    demand_pct: float
    wtp_pct: float
    urgency_pct: float
    gap_pct: float
    specificity_pct: float
    solvability_pct: float
    feasibility_pct: float
    evidence_coverage: str
    confidence_level: str  # evidence confidence only
    reasoning: str
    missing_evidence: str
    model_score: float = 0.0
    model_score_label: str = "MODEL_ESTIMATE — NOT VERIFIED PROFITABILITY"
    evidence_confidence: str = "HYPOTHESIS"
    source_coverage: str = ""
    verified_source_count: int = 0
    verified_field_count: int = 0

# --- Research Schemas ---
class ResearchSourceCreate(BaseModel):
    batch_id: Optional[int] = None
    title: str
    publisher: Optional[str] = "Unknown"
    author: Optional[str] = "Unknown"
    publication_date: Optional[str] = "Unknown"
    url: Optional[str] = None
    source_category: str
    content_text: str
    main_findings: Optional[str] = ""
    supporting_evidence: Optional[str] = ""
    important_statistics: Optional[str] = ""
    limitations: Optional[str] = ""
    reliability_assessment: ReliabilityCategory = ReliabilityCategory.MEDIUM
    reliability_rationale: Optional[str] = ""
    relevance_to_product: Optional[str] = ""

class ResearchFindingItem(BaseModel):
    finding_text: str
    category: str
    significance: str
    source_id: int

# --- UVZ & Transformation Schemas ---
class UVZReport(BaseModel):
    target_audience: str
    acute_problems: List[str]
    desired_outcomes: List[str]
    required_knowledge: List[str]
    accessible_solution_methods: List[str]
    market_demand_analysis: str
    unique_mechanism: str
    differentiation_strategy: str
    summary_verdict: str

class TransformationMap(BaseModel):
    before_situation: Dict[str, Any]
    after_situation: Dict[str, Any]
    milestones: List[Dict[str, Any]]

# --- Product Charter Schemas ---
class ProductCharter(BaseModel):
    product_title: str
    subtitle: str
    product_category: str
    target_audience: str
    customer_problem: str
    core_promise: str
    desired_outcome: str
    unique_mechanism: str
    differentiation: str
    product_description: str
    deliverables: List[str]
    evidence_base: str
    product_limitations: List[str]
    scope_exclusions: List[str]
    disclaimer_requirements: List[str]

# --- Outline & Chapters ---
class ChapterOutlineItem(BaseModel):
    chapter_number: int
    title: str
    purpose: str
    learning_objectives: List[str]
    key_topics: List[str]
    research_references: List[str]
    practical_exercises: str
    expected_outcome: str

class OutlineResponse(BaseModel):
    chapters: List[ChapterOutlineItem]

class ChapterContent(BaseModel):
    chapter_number: int
    title: str
    content: str
    word_count: int = 0
    is_locked: bool = False

# --- Quality Audit Schema ---
class QualityAuditReport(BaseModel):
    status: AuditStatus
    score: int = Field(ge=0, le=100)
    factual_accuracy_assessment: str
    unsupported_statements: List[str]
    repetition_issues: List[str]
    readability_score: str
    practical_value_assessment: str
    recommended_corrections: List[str]
    disclaimer_flags: List[str]
    summary_verdict: str

# --- Branding Schema ---
class BrandIdentity(BaseModel):
    product_name_options: List[str]
    subtitle_options: List[str]
    brand_story: str
    color_palettes: List[Dict[str, str]]
    font_pairings: List[Dict[str, str]]
    cover_concept: str
    interior_design_guidelines: str

# --- Sales Offer Schema ---
class SalesOfferPackage(BaseModel):
    headline: str
    subheadline: str
    problem_statement: str
    pain_points: List[str]
    solution_introduction: str
    core_benefits: List[str]
    whats_included: List[str]
    chapter_teasers: List[Dict[str, str]]
    unique_differentiation: str
    faqs: List[Dict[str, str]]
    call_to_action: str
    price_testing_hypotheses: List[Dict[str, str]]
    ethical_refund_policy: str
    marketplace_short_description: str
    promotional_social_posts: List[str]
    email_announcement_draft: str

