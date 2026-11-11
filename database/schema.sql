-- Product Synthesis Studio Database Schema
-- Version 1.0.0

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'United States',
    language TEXT NOT NULL DEFAULT 'English',
    target_customer TEXT,
    expertise_mode TEXT NOT NULL DEFAULT 'NON_EXPERT',
    product_category TEXT NOT NULL DEFAULT 'Business & Productivity',
    product_format TEXT NOT NULL DEFAULT 'Ebook',
    budget REAL DEFAULT 0.0,
    creator_interests TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    research_progress INTEGER DEFAULT 0,
    selected_opportunity_id INTEGER,
    requested_opportunity_count INTEGER DEFAULT 5,
    required_chapter_count INTEGER DEFAULT 11,
    target_chapter_words INTEGER DEFAULT 2200,
    min_chapter_words INTEGER DEFAULT 1000,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    batch_name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    batch_id INTEGER,
    title TEXT NOT NULL,
    publisher TEXT DEFAULT 'Unknown',
    author TEXT DEFAULT 'Unknown',
    publication_date TEXT DEFAULT 'Unknown',
    url TEXT,
    content_hash TEXT NOT NULL,
    source_category TEXT NOT NULL,
    raw_content TEXT,
    main_findings TEXT,
    supporting_evidence TEXT,
    important_statistics TEXT,
    limitations TEXT,
    reliability_assessment TEXT NOT NULL DEFAULT 'UNASSESSED',
    declared_reliability TEXT NOT NULL DEFAULT 'UNASSESSED',
    assessed_reliability TEXT NOT NULL DEFAULT 'UNASSESSED',
    assessment_score REAL DEFAULT 0.0,
    assessment_json TEXT,
    assessed_at TIMESTAMP,
    reliability_rationale TEXT,
    relevance_to_product TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(batch_id) REFERENCES research_batches(id) ON DELETE SET NULL,
    UNIQUE(project_id, content_hash)
);

CREATE TABLE IF NOT EXISTS research_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE CASCADE,
    UNIQUE(source_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS research_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    finding_text TEXT NOT NULL,
    category TEXT,
    significance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    exact_customer TEXT NOT NULL,
    customer_problem TEXT NOT NULL,
    problem_urgency TEXT NOT NULL,
    desired_outcome TEXT NOT NULL,
    demand_evidence TEXT,
    willingness_to_pay_evidence TEXT,
    existing_competitors TEXT,
    competitor_products TEXT,
    observed_prices TEXT,
    customer_complaints TEXT,
    missing_features TEXT,
    potential_solution TEXT NOT NULL,
    commercial_risks TEXT,
    ethical_considerations TEXT,
    evidence_confidence TEXT DEFAULT 'HYPOTHESIS',
    evidence_status TEXT DEFAULT 'HYPOTHESIS',
    evidence_summary TEXT,
    source_coverage_json TEXT,
    human_review_required INTEGER DEFAULT 0,
    citation_ids_json TEXT DEFAULT '[]',
    is_favorite INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS opportunity_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    source_id INTEGER,
    claim_field TEXT NOT NULL,
    claim_text TEXT,
    excerpt TEXT,
    relevance_score REAL DEFAULT 0.0,
    verified INTEGER DEFAULT 0,
    verification_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS opportunity_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL UNIQUE,
    weighted_score REAL NOT NULL,
    demand_score REAL NOT NULL,
    wtp_score REAL NOT NULL,
    urgency_score REAL NOT NULL,
    gap_score REAL NOT NULL,
    specificity_score REAL NOT NULL,
    solvability_score REAL NOT NULL,
    feasibility_score REAL NOT NULL,
    evidence_coverage TEXT,
    confidence_level TEXT,
    evidence_confidence TEXT,
    model_score_label TEXT DEFAULT 'MODEL_ESTIMATE',
    source_coverage_json TEXT,
    verified_source_count INTEGER DEFAULT 0,
    verified_field_count INTEGER DEFAULT 0,
    reasoning TEXT,
    missing_evidence TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT,
    website TEXT,
    product_format TEXT DEFAULT 'Ebook',
    observed_price TEXT,
    main_features TEXT,
    customer_reviews TEXT,
    customer_complaints TEXT,
    strengths TEXT,
    weaknesses TEXT,
    evidence_sources TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transformation_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    milestones_json TEXT NOT NULL,
    product_format TEXT DEFAULT 'Ebook',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_charters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    product_title TEXT NOT NULL,
    subtitle TEXT NOT NULL,
    product_category TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    customer_problem TEXT NOT NULL,
    core_promise TEXT NOT NULL,
    desired_outcome TEXT NOT NULL,
    unique_mechanism TEXT NOT NULL,
    differentiation TEXT NOT NULL,
    product_description TEXT NOT NULL,
    deliverables_json TEXT NOT NULL,
    evidence_base TEXT NOT NULL,
    product_limitations_json TEXT NOT NULL,
    scope_exclusions_json TEXT NOT NULL,
    disclaimer_requirements_json TEXT NOT NULL,
    product_format TEXT DEFAULT 'Ebook',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_outlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    product_format TEXT DEFAULT 'Ebook',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    purpose TEXT,
    learning_objectives_json TEXT,
    key_topics_json TEXT,
    research_references_json TEXT,
    practical_exercises TEXT,
    expected_outcome TEXT,
    content TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    target_word_count INTEGER DEFAULT 2200,
    planned_word_count INTEGER DEFAULT 0,
    actual_word_count INTEGER DEFAULT 0,
    structural_complete INTEGER DEFAULT 0,
    evidence_complete INTEGER DEFAULT 0,
    editorial_status TEXT DEFAULT 'NOT_REVIEWED',
    approval_status TEXT DEFAULT 'UNREVIEWED',
    editorial_json TEXT,
    validation_status TEXT DEFAULT 'PENDING',
    validation_json TEXT,
    is_locked INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, chapter_number)
);

CREATE TABLE IF NOT EXISTS chapter_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL,
    revision_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quality_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_id INTEGER,
    status TEXT NOT NULL,
    score INTEGER NOT NULL,
    factual_accuracy_assessment TEXT,
    unsupported_statements_json TEXT,
    repetition_issues_json TEXT,
    readability_score TEXT,
    practical_value_assessment TEXT,
    recommended_corrections_json TEXT,
    disclaimer_flags_json TEXT,
    summary_verdict TEXT,
    deterministic_checks_json TEXT,
    issues_json TEXT,
    editorial_rubric_json TEXT,
    editorial_advisory INTEGER DEFAULT 1,
    recommendations_json TEXT,
    human_review_required INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    brand_name TEXT NOT NULL,
    brand_story TEXT,
    color_palette_json TEXT,
    font_pairing_json TEXT,
    cover_concept TEXT,
    interior_guidelines TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    export_type TEXT NOT NULL, -- PDF, DOCX, etc.
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER,
    completeness_status TEXT DEFAULT 'WORKING_DRAFT',
    completeness_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sales_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    headline TEXT NOT NULL,
    subheadline TEXT NOT NULL,
    problem_statement TEXT,
    pain_points_json TEXT,
    solution_introduction TEXT,
    core_benefits_json TEXT,
    whats_included_json TEXT,
    chapter_teasers_json TEXT,
    unique_differentiation TEXT,
    faqs_json TEXT,
    call_to_action TEXT,
    price_testing_hypotheses_json TEXT,
    ethical_refund_policy TEXT,
    marketplace_short_description TEXT,
    promotional_social_posts_json TEXT,
    email_announcement_draft TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS distribution_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    channels_json TEXT,
    community_plan TEXT,
    social_plan TEXT,
    search_plan TEXT,
    launch_checklist_json TEXT,
    payment_platform_analysis TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analytics_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    record_date DATE NOT NULL,
    visits INTEGER DEFAULT 0,
    sales INTEGER DEFAULT 0,
    revenue REAL DEFAULT 0.0,
    customer_questions INTEGER DEFAULT 0,
    refunds INTEGER DEFAULT 0,
    feedback_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chapter_autosaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    draft_content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SAVED',
    error_message TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS backup_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    reason TEXT NOT NULL,
    size_bytes INTEGER DEFAULT 0,
    integrity_ok INTEGER DEFAULT 0,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_research_chunks_project ON research_chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_research_chunks_source ON research_chunks(source_id);

-- P2: structured chapter plans, sections and evidence traceability
CREATE TABLE IF NOT EXISTS chapter_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL UNIQUE,
    plan_json TEXT NOT NULL,
    planned_words INTEGER DEFAULT 0,
    target_words INTEGER DEFAULT 0,
    min_words INTEGER DEFAULT 0,
    status TEXT DEFAULT 'PLANNED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chapter_plans_project ON chapter_plans(project_id);

CREATE TABLE IF NOT EXISTS chapter_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    heading TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    plan_notes TEXT,
    content TEXT DEFAULT '',
    word_budget INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'PLANNED',
    model TEXT,
    generation_time_ms REAL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
    UNIQUE(chapter_id, section_key)
);
CREATE INDEX IF NOT EXISTS idx_chapter_sections_chapter ON chapter_sections(chapter_id);

CREATE TABLE IF NOT EXISTS chapter_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    source_id INTEGER,
    chunk_id INTEGER,
    chunk_index INTEGER,
    relevance_score REAL DEFAULT 0.0,
    quality_score REAL DEFAULT 0.0,
    citation_label TEXT,
    excerpt TEXT,
    used INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chapter_sources_chapter ON chapter_sources(chapter_id);

CREATE TABLE IF NOT EXISTS batch_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    mode TEXT DEFAULT 'ALL_REMAINING',
    total_chapters INTEGER DEFAULT 0,
    processed_chapters INTEGER DEFAULT 0,
    failed_chapters INTEGER DEFAULT 0,
    current_chapter INTEGER,
    state_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_batch_sessions_project ON batch_sessions(project_id);

CREATE TABLE IF NOT EXISTS model_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    model TEXT NOT NULL,
    task TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    generation_ms REAL DEFAULT 0.0,
    metrics_json TEXT,
    rubric_json TEXT,
    json_valid INTEGER DEFAULT 0,
    citation_compliance REAL DEFAULT 0.0,
    structure_score REAL DEFAULT 0.0,
    word_count INTEGER DEFAULT 0,
    topic_relevance REAL DEFAULT 0.0,
    instruction_compliance REAL DEFAULT 0.0,
    human_score REAL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS editorial_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chapter_id INTEGER,
    report_id INTEGER,
    code TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT DEFAULT 'MEDIUM',
    message TEXT NOT NULL,
    source TEXT DEFAULT 'DETERMINISTIC',
    status TEXT DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
    FOREIGN KEY(report_id) REFERENCES quality_reports(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_editorial_recommendations_project
    ON editorial_recommendations(project_id);

CREATE TABLE IF NOT EXISTS application_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

