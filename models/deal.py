from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class PricingOption(BaseModel):
    name: str = ""
    original_price: float | None = None
    deal_price: float | None = None
    discount_pct: float | None = None
    savings: float | None = None


class ImageData(BaseModel):
    count: int = 0
    alt_texts: list[str] = Field(default_factory=list)
    quality_assessment: str = ""


class Review(BaseModel):
    rating: float | None = None
    text: str = ""
    date: str | None = None
    author: str | None = None


class FAQ(BaseModel):
    question: str = ""
    answer: str = ""


class SEOData(BaseModel):
    meta_title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    h2s: list[str] = Field(default_factory=list)
    schema_types: list[str] = Field(default_factory=list)
    canonical_url: str | None = None


class TrustSignals(BaseModel):
    review_count: int | None = None
    avg_rating: float | None = None
    num_sold: str | None = None
    has_guarantee: bool = False
    badges: list[str] = Field(default_factory=list)


class UrgencyElements(BaseModel):
    has_countdown: bool = False
    limited_quantity_text: str | None = None
    selling_fast: bool = False


class DealAudit(BaseModel):
    url: str
    slug: str
    title: str = ""
    subtitle: str | None = None
    merchant_name: str = ""
    city: str = ""
    category: str = ""
    pricing_options: list[PricingOption] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    fine_print: list[str] = Field(default_factory=list)
    faqs: list[FAQ] = Field(default_factory=list)
    images: ImageData = Field(default_factory=ImageData)
    script_count: int = 0
    stylesheet_count: int = 0
    mobile_viewport_meta: str | None = None
    reviews: list[Review] = Field(default_factory=list)
    seo: SEOData = Field(default_factory=SEOData)
    trust_signals: TrustSignals = Field(default_factory=TrustSignals)
    urgency: UrgencyElements = Field(default_factory=UrgencyElements)
    raw_html_length: int = 0
    scraped_at: str = ""


class ResearchData(BaseModel):
    url: str
    slug: str
    competitor_prices: list[dict[str, Any]] = Field(default_factory=list)
    merchant_direct_price: str | None = None
    yelp_rating: float | None = None
    yelp_review_count: int | None = None
    google_rating: float | None = None
    sentiment_themes: list[dict[str, Any]] = Field(default_factory=list)
    review_quotes: list[dict[str, Any]] = Field(default_factory=list)
    value_verdict: str = ""
    category_typical_discount: str = ""
    content_gaps: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    researched_at: str = ""


class OptimizationProposal(BaseModel):
    url: str
    slug: str
    title_rewrite: str = ""
    title_reasoning: str = ""
    pricing_frame: str = ""
    highlights_rewrite: list[str] = Field(default_factory=list)
    missing_content: list[str] = Field(default_factory=list)
    image_recommendations: list[str] = Field(default_factory=list)
    seo_improvements: dict[str, Any] = Field(default_factory=dict)
    competitive_positioning: str = ""
    priority_ranking: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str = ""
