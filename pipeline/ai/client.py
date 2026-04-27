from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import anthropic

from models.deal import DealAudit, ResearchData, OptimizationProposal
from pipeline.ai.prompts import (
    AUDIT_ANALYSIS_SYSTEM,
    RESEARCH_SYNTHESIS_SYSTEM,
    PROPOSAL_SYSTEM,
    build_audit_analysis_user,
    build_research_synthesis_user,
    build_proposal_user,
)

logger = logging.getLogger(__name__)
usage_logger = logging.getLogger("usage")

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _call(model: str, system: str, user: str, max_tokens: int = 4096) -> tuple[str, dict]:
    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    content = response.content[0].text if response.content else ""
    usage = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    usage_logger.info(
        "model=%s input_tokens=%d output_tokens=%d",
        model, usage["input_tokens"], usage["output_tokens"],
    )
    return content, usage


def _parse_json(text: str) -> dict:
    clean = _strip_fences(text)
    return json.loads(clean)


def analyze_audit(audit: DealAudit) -> dict:
    audit_json = audit.model_dump_json(indent=2)
    user_msg = build_audit_analysis_user(audit_json)

    try:
        raw, _ = _call(HAIKU, AUDIT_ANALYSIS_SYSTEM, user_msg)
        return _parse_json(raw)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed on first attempt for audit analysis, retrying...")
        try:
            raw, _ = _call(HAIKU, AUDIT_ANALYSIS_SYSTEM, user_msg + "\n\nIMPORTANT: Return ONLY raw JSON, no fences.")
            return _parse_json(raw)
        except Exception as e:
            logger.error("analyze_audit failed: %s", e)
            return {"error": str(e)}
    except Exception as e:
        logger.error("analyze_audit failed: %s", e)
        return {"error": str(e)}


def synthesize_research(raw_data: dict, audit: DealAudit) -> ResearchData:
    import json as _json
    audit_json = audit.model_dump_json(indent=2)
    raw_data_json = _json.dumps(raw_data, indent=2, default=str)[:8000]

    user_msg = build_research_synthesis_user(audit_json, raw_data_json)

    try:
        raw, _ = _call(HAIKU, RESEARCH_SYNTHESIS_SYSTEM, user_msg, max_tokens=4096)
        data = _parse_json(raw)
        # Prefer AI-extracted values; fall back to values measured directly by the researcher
        # (AI sometimes fails to pass through numeric fields even when present in raw_data)
        yelp_rating = data.get("yelp_rating") or raw_data.get("yelp_rating")
        yelp_review_count = data.get("yelp_review_count") or raw_data.get("yelp_review_count")
        return ResearchData(
            url=audit.url,
            slug=audit.slug,
            competitor_prices=data.get("competitor_prices", []),
            merchant_direct_price=data.get("merchant_direct_price"),
            yelp_rating=yelp_rating,
            yelp_review_count=yelp_review_count,
            google_rating=data.get("google_rating"),
            sentiment_themes=data.get("sentiment_themes", []),
            review_quotes=data.get("review_quotes", []),
            value_verdict=data.get("value_verdict", ""),
            category_typical_discount=data.get("category_typical_discount", ""),
            content_gaps=data.get("content_gaps", []),
            sources=data.get("sources", []),
            researched_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        logger.error("synthesize_research failed: %s", e, exc_info=True)
        return ResearchData(
            url=audit.url,
            slug=audit.slug,
            value_verdict=f"[Synthesis error: {e}]",
            researched_at=datetime.now(timezone.utc).isoformat(),
        )


def generate_proposal(
    audit: DealAudit, research: ResearchData, analysis: dict
) -> OptimizationProposal:
    import json as _json
    audit_json = audit.model_dump_json(indent=2)
    research_json = research.model_dump_json(indent=2)
    analysis_json = _json.dumps(analysis, indent=2, default=str)

    user_msg = build_proposal_user(audit_json, research_json, analysis_json)

    def _attempt() -> OptimizationProposal:
        raw, _ = _call(SONNET, PROPOSAL_SYSTEM, user_msg, max_tokens=8096)
        data = _parse_json(raw)
        return OptimizationProposal(
            url=audit.url,
            slug=audit.slug,
            title_rewrite=data.get("title_rewrite", ""),
            title_reasoning=data.get("title_reasoning", ""),
            pricing_frame=data.get("pricing_frame", ""),
            highlights_rewrite=data.get("highlights_rewrite", []),
            missing_content=data.get("missing_content", []),
            image_recommendations=data.get("image_recommendations", []),
            seo_improvements=data.get("seo_improvements", {}),
            competitive_positioning=data.get("competitive_positioning", ""),
            priority_ranking=data.get("priority_ranking", []),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        return _attempt()
    except Exception as e:
        logger.warning("generate_proposal first attempt failed: %s — retrying", e)
        try:
            return _attempt()
        except Exception as e2:
            logger.error("generate_proposal failed after retry: %s", e2, exc_info=True)
            return OptimizationProposal(
                url=audit.url,
                slug=audit.slug,
                title_rewrite=f"[Error: {e2}]",
                title_reasoning="Proposal generation failed. See logs.",
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
