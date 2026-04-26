"""Prompt templates for the Groupon optimizer AI pipeline."""

AUDIT_ANALYSIS_SYSTEM = (
    "You are a conversion rate optimization expert analyzing Groupon deal pages. "
    "Return only valid JSON with no markdown fences, no explanation."
)

AUDIT_ANALYSIS_USER_TEMPLATE = (
    "Analyze this Groupon deal audit JSON and return a JSON object with exactly these keys:\n\n"
    '{{\n'
    '  "title_clarity_score": <integer 1-10>,\n'
    '  "title_clarity_reasoning": "<explain score citing actual title text>",\n'
    '  "trust_signal_strength": <integer 1-10>,\n'
    '  "trust_signal_reasoning": "<explain citing review_count, avg_rating, num_sold values>",\n'
    '  "urgency_effectiveness": <integer 1-10>,\n'
    '  "urgency_reasoning": "<explain citing has_countdown, selling_fast, limited_quantity_text values>",\n'
    '  "image_quality_assessment": "<assess the alt_texts list and count for quality signals>",\n'
    '  "top_3_weaknesses": [\n'
    '    {{"finding": "<weakness>", "evidence": "<specific field value from audit JSON>"}},\n'
    '    {{"finding": "<weakness>", "evidence": "<specific field value from audit JSON>"}},\n'
    '    {{"finding": "<weakness>", "evidence": "<specific field value from audit JSON>"}}\n'
    '  ],\n'
    '  "top_3_strengths": [\n'
    '    {{"finding": "<strength>", "evidence": "<specific field value from audit JSON>"}},\n'
    '    {{"finding": "<strength>", "evidence": "<specific field value from audit JSON>"}},\n'
    '    {{"finding": "<strength>", "evidence": "<specific field value from audit JSON>"}}\n'
    '  ],\n'
    '  "faq_assessment": "<present/missing/weak and why it matters for conversion, cite faqs count>"\n'
    '}}\n\n'
    "CRITICAL: Every weakness and strength MUST cite a specific field value from the audit JSON below.\n\n"
    "AUDIT JSON:\n{audit_json}"
)

RESEARCH_SYNTHESIS_SYSTEM = (
    "You are a market research analyst producing structured competitive intelligence. "
    "Return only valid JSON with no markdown fences, no explanation."
)

RESEARCH_SYNTHESIS_USER_TEMPLATE = (
    "Synthesize the raw research data below into a JSON object for this Groupon deal.\n\n"
    "CRITICAL RULES:\n"
    "- Every competitor_prices entry MUST include name, price (exact dollar amount), and source_url\n"
    "- Every review_quotes entry MUST be verbatim text, not paraphrased\n"
    "- value_verdict MUST explicitly compare the Groupon deal price to merchant_direct_price and top competitor prices by name\n"
    "- No generic statements — every claim must cite a source or data point\n"
    "- If data is truly unavailable, say 'Not found in research' rather than inventing numbers\n\n"
    "Return JSON with exactly these keys:\n"
    '{{\n'
    '  "competitor_prices": [{{"name": "<business name>", "price": "<$XX>", "source_url": "<url>", "notes": "<optional context>"}}],\n'
    '  "merchant_direct_price": "<$XX or Not found>",\n'
    '  "yelp_rating": <float or null>,\n'
    '  "yelp_review_count": <int or null>,\n'
    '  "google_rating": <float or null>,\n'
    '  "sentiment_themes": [{{"theme": "<theme>", "sentiment": "positive|negative|mixed", "quote": "<verbatim quote>", "source_url": "<url>"}}],\n'
    '  "review_quotes": [{{"text": "<verbatim quote>", "rating": <float or null>, "source": "<yelp|google|groupon>", "url": "<url>"}}],\n'
    '  "value_verdict": "<Is the Groupon price a good deal? Name specific businesses and prices>",\n'
    '  "category_typical_discount": "<e.g. 40-60 percent off for spa services in Chicago>",\n'
    '  "content_gaps": ["<gap 1>", "<gap 2>"],\n'
    '  "sources": ["<url1>", "<url2>"]\n'
    '}}\n\n'
    "DEAL AUDIT:\n{audit_json}\n\n"
    "RAW RESEARCH DATA:\n{raw_data_json}"
)

PROPOSAL_SYSTEM = (
    "You are a senior e-commerce optimization strategist writing proposals for the CEO of Groupon. "
    "Return only valid JSON with no markdown fences, no explanation."
)

PROPOSAL_USER_TEMPLATE = (
    "Generate a comprehensive optimization proposal for this Groupon deal page.\n\n"
    "CRITICAL RULES:\n"
    "- Every priority_ranking entry MUST include a data_citation naming a specific number, quote, competitor name, or metric\n"
    "- If you cannot cite specific data for a recommendation, do not include it in priority_ranking\n"
    "- title_rewrite MUST be a complete, ready-to-publish title string (not a template)\n"
    "- competitive_positioning MUST name at least 2 specific competitors by name with their prices\n"
    "- title_reasoning MUST cite specific data (current title word count, missing keywords, competitor patterns)\n"
    "- All pricing references must use exact numbers from the provided data\n\n"
    "Return JSON with exactly these keys:\n"
    '{{\n'
    '  "title_rewrite": "<complete ready-to-publish title>",\n'
    '  "title_reasoning": "<why this title, citing specific data>",\n'
    '  "pricing_frame": "<how to reframe the pricing/value proposition with specific numbers>",\n'
    '  "highlights_rewrite": ["<rewritten highlight 1>", "<rewritten highlight 2>"],\n'
    '  "missing_content": ["<content gap 1>", "<content gap 2>"],\n'
    '  "image_recommendations": ["<recommendation 1>", "<recommendation 2>"],\n'
    '  "seo_improvements": {{\n'
    '    "meta_title": "<proposed meta title>",\n'
    '    "meta_description": "<proposed meta description under 160 chars>",\n'
    '    "h1": "<proposed H1>",\n'
    '    "schema": "<schema type to add/fix and why>"\n'
    '  }},\n'
    '  "competitive_positioning": "<position vs competitors, name at least 2 by name with their prices>",\n'
    '  "priority_ranking": [\n'
    '    {{\n'
    '      "rank": 1,\n'
    '      "change": "<specific actionable change>",\n'
    '      "reasoning": "<why this matters>",\n'
    '      "data_citation": "<specific number, quote, or metric from the provided data>",\n'
    '      "expected_impact": "<e.g. +15% CTR, +20% conversion>"\n'
    '    }}\n'
    '  ]\n'
    '}}\n\n'
    "DEAL AUDIT:\n{audit_json}\n\n"
    "RESEARCH DATA:\n{research_json}\n\n"
    "AUDIT ANALYSIS:\n{analysis_json}"
)


def build_audit_analysis_user(audit_json: str) -> str:
    return AUDIT_ANALYSIS_USER_TEMPLATE.format(audit_json=audit_json)


def build_research_synthesis_user(audit_json: str, raw_data_json: str) -> str:
    return RESEARCH_SYNTHESIS_USER_TEMPLATE.format(
        audit_json=audit_json,
        raw_data_json=raw_data_json,
    )


def build_proposal_user(audit_json: str, research_json: str, analysis_json: str) -> str:
    return PROPOSAL_USER_TEMPLATE.format(
        audit_json=audit_json,
        research_json=research_json,
        analysis_json=analysis_json,
    )
