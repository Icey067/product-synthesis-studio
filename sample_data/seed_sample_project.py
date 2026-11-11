"""
Creates the mandatory Sample Project (Part 24) demonstrating end-to-end functionality:
- Country: India
- Mode: Non-expert
- Language: English
- Product Format: Ebook
- Niche: Digital growth for local micro-businesses
- Product concept: A practical 30-day local business digital growth playbook
All sample data is explicitly marked as synthetic demonstration data.
"""

from database.db import init_db
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.market_service import MarketService
from services.charter_service import CharterService
from services.transformation_service import TransformationService
from services.outline_service import OutlineService
from services.generation_service import GenerationService
from services.export_service import ExportService
from models.schemas import ProjectCreate
from models.enums import ExpertiseMode, ProductFormat

def seed_sample_project() -> int:
    init_db()

    # 1. Project Creation
    project_data = ProjectCreate(
        name="Local Business 30-Day Digital Growth Playbook",
        country="India",
        language="English",
        target_customer="Independent local retail and service shop owners (e.g. bakeries, neighborhood clinics, boutique stores)",
        expertise_mode=ExpertiseMode.NON_EXPERT,
        product_category="Business & Productivity",
        product_format=ProductFormat.EBOOK,
        budget=0.0,
        creator_interests="Simple digital tools, WhatsApp Business marketing, Google Maps optimization, customer retention",
    )
    project_id = ProjectService.create_project(project_data)

    # 2. Ingest Sample Research Sources (clearly marked as synthetic demonstration data)
    ResearchService.add_source(
        project_id=project_id,
        title="[Sample Research] MSME Digital Adoption Report: Tier-2 Indian Retailers",
        source_category="SAMPLE_REPORT",
        content_text=(
            "Key finding: 78% of local storefront owners experience high customer footfall churn "
            "because they lack basic digital follow-up systems. Storefronts implementing simple automated "
            "WhatsApp Business greeting broadcasts and Google Business Profile weekly updates observed a "
            "22% increase in repeat customer visits within 30 days. High friction occurs when small business "
            "owners are forced to use complex CRM software rather than conversational smartphone tools."
        ),
        publisher="MSME Digital Research Institute (Sample Data)",
        author="Dr. R. Sharma (Illustrative)",
        publication_date="2024",
        reliability_assessment="HIGH",
    )

    ResearchService.add_source(
        project_id=project_id,
        title="[Sample Case Survey] Local Storefront Customer Retention Drivers",
        source_category="SAMPLE_SURVEY",
        content_text=(
            "Empirical observation of 120 neighborhood businesses revealed that customer reviews on Google Maps "
            "are the single highest driver of spontaneous foot traffic for local services (accounting for 41% of first-time visits). "
            "However, 83% of merchants never systematically request reviews or display direct QR codes at checkout."
        ),
        publisher="Retail Commerce Observatory (Sample Data)",
        author="K. Patel (Illustrative)",
        publication_date="2024",
        reliability_assessment="MEDIUM",
    )

    # 3. Create Sample Opportunity & Score
    opp_dict = {
        "name": "Local Storefront 30-Day Digital Repeat-Visit Engine",
        "exact_customer": "Neighborhood retailers and clinic owners who have zero technical staff",
        "customer_problem": "Losing repeat customers to quick-commerce apps; low discoverability on local maps",
        "problem_urgency": "High",
        "desired_outcome": "Systematic 20-30% boost in repeat visits through free smartphone-based local marketing",
        "demand_evidence": "Observed high query volume for 'how to get local customers on WhatsApp' and Google Business optimization",
        "willingness_to_pay_evidence": "Small business owners regularly pay $20-$100 for local flyers and basic marketing assistance",
        "existing_competitors": "Generic enterprise marketing courses (too complex) and expensive digital agencies",
        "competitor_products": "Digital Marketing for Beginners (Generic video course, $99)",
        "observed_prices": "$19 - $39 for practical guides",
        "customer_complaints": "Too theoretical, requires hiring designers, doesn't reflect local cash/WhatsApp reality",
        "missing_features": "Day-by-day 30-day checklist; copy-paste WhatsApp message templates; QR review stands",
        "potential_solution": "A plug-and-play 30-day digital action playbook designed specifically for smartphone use",
        "commercial_risks": "Distribution reaches shopkeepers through peer recommendations and local trade networks",
        "ethical_considerations": "Must clearly emphasize that outcomes depend on consistent execution, no revenue guarantees",
        "evidence_confidence": "HIGH",
    }

    from services.scoring_service import ScoringService
    scores_dict = {
        "demand_score": 9.0,
        "willingness_to_pay_score": 8.0,
        "urgency_score": 8.5,
        "competitor_gap_score": 8.5,
        "customer_specificity_score": 9.0,
        "practical_solvability_score": 9.5,
        "feasibility_score": 9.0,
    }
    score_res = ScoringService.calculate_score(scores_dict, opp_dict)

    from database.db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO opportunities (
                project_id, name, exact_customer, customer_problem, problem_urgency,
                desired_outcome, demand_evidence, willingness_to_pay_evidence,
                existing_competitors, competitor_products, observed_prices,
                customer_complaints, missing_features, potential_solution,
                commercial_risks, ethical_considerations, evidence_confidence, is_favorite
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                project_id, opp_dict["name"], opp_dict["exact_customer"], opp_dict["customer_problem"],
                opp_dict["problem_urgency"], opp_dict["desired_outcome"], opp_dict["demand_evidence"],
                opp_dict["willingness_to_pay_evidence"], opp_dict["existing_competitors"],
                opp_dict["competitor_products"], opp_dict["observed_prices"], opp_dict["customer_complaints"],
                opp_dict["missing_features"], opp_dict["potential_solution"], opp_dict["commercial_risks"],
                opp_dict["ethical_considerations"], opp_dict["evidence_confidence"]
            )
        )
        opp_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO opportunity_scores (
                opportunity_id, weighted_score, demand_score, wtp_score, urgency_score,
                gap_score, specificity_score, solvability_score, feasibility_score,
                evidence_coverage, confidence_level, reasoning, missing_evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opp_id, score_res.weighted_score, 9.0, 8.0, 8.5, 8.5, 9.0, 9.5, 9.0,
                score_res.evidence_coverage, score_res.confidence_level,
                "Strong empirical demand for micro-business smartphone marketing playbooks.",
                "None for initial blueprint."
            )
        )
        cur.execute("UPDATE projects SET selected_opportunity_id = ? WHERE id = ?", (opp_id, project_id))

    # 4. Transformation Map
    t_map = {
        "before_situation": {
            "current_situation": "Shop owners depend entirely on unpredictable walk-in foot traffic.",
            "frustrations": [
                "Customers buy once and never come back.",
                "Competitor chains and quick-delivery apps erode retail margins.",
                "Marketing guides are too theoretical and demand expensive software."
            ],
            "common_mistakes": [
                "Spending money on printed newspaper pamphlets with zero tracking.",
                "Failing to collect repeat customer contact numbers at the billing counter."
            ],
            "obstacles": ["Lack of technical skills and lack of time outside daily shop duties."]
        },
        "after_situation": {
            "desired_situation": "A thriving, loyal neighborhood customer base reachable instantly via WhatsApp broadcasts.",
            "new_capabilities": [
                "Ability to rank at the top of local Google Maps searches for neighborhood queries.",
                "Automated review collection generating 15+ verified 5-star ratings monthly.",
                "Weekend offer broadcast system generating immediate repeat sales."
            ],
            "measurable_results": ["20% to 30% increase in monthly repeat customer sales within 30 days."]
        },
        "milestones": [
            {
                "phase_number": 1,
                "title": "Week 1: The Zero-Cost Local Search Foundation",
                "starting_point": "Shop is invisible or has incorrect hours on Google Maps.",
                "key_actions": [
                    "Claim and fully optimize Google Business Profile with smartphone photos.",
                    "Set up counter QR stand for instant 5-star reviews."
                ],
                "obstacles_and_remedies": "Verification postcard delay: Use instant smartphone video verification.",
                "measurable_progress": "10 new verified reviews and top 3 ranking in local search."
            },
            {
                "phase_number": 2,
                "title": "Week 2: The VIP WhatsApp Broadcast System",
                "starting_point": "No customer database collected.",
                "key_actions": [
                    "Deploy billing counter incentive (e.g. free sample or 5% discount for WhatsApp club).",
                    "Configure WhatsApp Business catalog and quick replies."
                ],
                "obstacles_and_remedies": "Customer hesitation: Offer immediate value at the moment of payment.",
                "measurable_progress": "150 loyal customers enrolled in VIP club."
            }
        ]
    }
    TransformationService.save_map(project_id, t_map)

    # 5. Product Charter
    charter_data = {
        "product_title": "The 30-Day Local Business Digital Growth Playbook",
        "subtitle": "How Neighborhood Storefronts Attract and Retain Repeat Customers Using Free Smartphone Tools",
        "product_category": "Business & Productivity",
        "target_audience": "Independent retail store owners, cafe owners, boutique clinic operators, and neighborhood service providers",
        "customer_problem": "Micro-businesses lack technical staff and struggle with customer retention against large online retailers.",
        "core_promise": "Build an automated local digital customer acquisition and retention system in 30 days using only your smartphone.",
        "desired_outcome": "Consistent repeat customer visits and a top-ranked local map presence without paid ads.",
        "unique_mechanism": "The 'Counter-to-Cloud' 15-minute daily operational playbook.",
        "differentiation": "Zero technical jargon, zero paid software dependencies; built exclusively around free apps (Google Maps & WhatsApp Business).",
        "product_description": (
            "A step-by-step practical implementation guide for busy local business owners. "
            "Divided into 4 weekly action phases with daily 15-minute checklists, ready-to-use message templates, and review QR strategies."
        ),
        "deliverables": [
            "Complete 11-Chapter Implementation Ebook (PDF & Word)",
            "Ready-to-print Checkout Counter Review QR Stand Template",
            "30 Copy-Paste WhatsApp Broadcast Message Templates",
            "Weekly Local Footfall & Revenue Tracking Worksheet"
        ],
        "evidence_base": "Synthesized from MSME digital adoption studies and storefront customer retention surveys.",
        "product_limitations": [
            "Requires active consistent execution by shop owner or cashier.",
            "Designed for local geographic retail and services, not worldwide B2B software."
        ],
        "scope_exclusions": ["Paid Google Ads or Facebook Ads management; custom website development."],
        "disclaimer_requirements": [
            "Results will vary according to merchant product quality, pricing, and local footfall dynamics.",
            "This guide provides operational marketing frameworks and does not guarantee specific revenue targets."
        ]
    }
    from database.db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        import json
        cur.execute(
            """
            INSERT OR REPLACE INTO product_charters (
                project_id, product_title, subtitle, product_category, target_audience,
                customer_problem, core_promise, desired_outcome, unique_mechanism,
                differentiation, product_description, deliverables_json, evidence_base,
                product_limitations_json, scope_exclusions_json, disclaimer_requirements_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, charter_data["product_title"], charter_data["subtitle"], charter_data["product_category"],
                charter_data["target_audience"], charter_data["customer_problem"], charter_data["core_promise"],
                charter_data["desired_outcome"], charter_data["unique_mechanism"], charter_data["differentiation"],
                charter_data["product_description"], json.dumps(charter_data["deliverables"]), charter_data["evidence_base"],
                json.dumps(charter_data["product_limitations"]), json.dumps(charter_data["scope_exclusions"]),
                json.dumps(charter_data["disclaimer_requirements"])
            )
        )

    # 6. Outline & Chapters
    sample_chapters = [
        (
            1,
            "The Local Retail Battlefield & The 30-Day Opportunity",
            "Diagnose why neighborhood stores lose repeat customers and how smartphone marketing reverses the trend.",
            (
                "## Chapter 1: The Local Retail Battlefield & The 30-Day Opportunity\n\n"
                "As an independent business owner, your greatest asset is proximity and personal trust. "
                "However, empirical research reveals that over 78% of local storefront owners lose customer momentum "
                "simply because there is no follow-up mechanism once a patron leaves the cash counter [Source-1].\n\n"
                "### The 15-Minute Daily Habit\n"
                "You do not need to spend hours creating complex graphics or managing complicated software. "
                "The entire 'Counter-to-Cloud' system requires only 15 minutes of disciplined daily action.\n\n"
                "### Practical Action Checklist\n"
                "- [ ] Audit your current storefront listing on Google Maps using your phone.\n"
                "- [ ] Download and verify WhatsApp Business on a dedicated storefront device.\n"
                "- [ ] Review your average customer repeat interval.\n\n"
                "### Common Pitfalls to Avoid\n"
                "Do not spend money on printed paper flyers before your digital search profile is verified and active."
            )
        ),
        (
            2,
            "Local Search Optimization: Dominating Neighborhood Maps",
            "Step-by-step walkthrough of Google Business Profile setup, photo updates, and instant reviews.",
            (
                "## Chapter 2: Local Search Optimization: Dominating Neighborhood Maps\n\n"
                "When a potential customer in your neighborhood searches for 'best bakery near me' or 'physiotherapist nearby', "
                "the top three map listings capture over 65% of all calls and direct visits [Source-2].\n\n"
                "### The Review QR System\n"
                "Place a small stand directly next to your UPI payment QR code with the words: "
                "'Loved your experience? Leave an honest review and get 5% off your next visit.'\n\n"
                "### Implementation Worksheet\n"
                "1. Generate your short Google Review link via the Business Profile app.\n"
                "2. Create a clean printed QR stand for your checkout counter.\n"
                "3. Train counter staff to politely request reviews from happy regulars."
            )
        ),
        (
            3,
            "The WhatsApp Business VIP Club: Turning One-Time Walk-ins into Regulars",
            "How to ethically collect phone numbers and send high-converting weekly offers without spamming.",
            (
                "## Chapter 3: The WhatsApp Business VIP Club\n\n"
                "Permission-based messaging is the highest-converting digital channel available to micro-retailers. "
                "When executed with genuine respect and high value, broadcast open rates routinely exceed 90%.\n\n"
                "### The 3 Golden Rules of Local Messaging\n"
                "1. **Never spam:** Limit broadcasts to at most once per week.\n"
                "2. **Always provide tangible value:** Exclusive member discounts, secret menu items, or early access.\n"
                "3. **Respect opt-outs immediately:** If a customer asks to stop, remove them politely."
            )
        )
    ]

    for num, title, purpose, content in sample_chapters:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO chapters (
                    project_id, chapter_number, title, purpose, content, word_count, is_locked
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (project_id, num, title, purpose, content, len(content.split()))
            )

    # 7. Generate PDF & DOCX Exports for the Sample Project
    charter = CharterService.get_charter(project_id)
    chapters = OutlineService.get_chapters(project_id)
    sources = ResearchService.get_sources(project_id)
    ExportService.generate_pdf(project_id, charter, chapters, sources)
    ExportService.generate_docx(project_id, charter, chapters, sources)

    return project_id

if __name__ == "__main__":
    p_id = seed_sample_project()
    print(f"Sample project successfully seeded with ID: {p_id}")

