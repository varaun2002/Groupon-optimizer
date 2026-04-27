from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from models.deal import DealAudit, ResearchData

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _delay() -> None:
    time.sleep(random.uniform(1.0, 2.0))


# ── SerpAPI (Google Search) ───────────────────────────────────────────────────

def _serp_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Search via SerpAPI (real Google results with snippets)."""
    api_key = os.getenv("SERP_API_KEY", "")
    if not api_key:
        logger.warning("SERP_API_KEY not set — skipping search for %r", query)
        return []
    _delay()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google",
                    "q": query,
                    "api_key": api_key,
                    "num": num_results,
                    "gl": "us",
                    "hl": "en",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for r in data.get("organic_results", [])[:num_results]:
                    results.append({
                        "url": r.get("link", ""),
                        "title": r.get("title", ""),
                        "snippet": r.get("snippet", ""),
                        "query": query,
                    })
                logger.info("SerpAPI %r -> %d results", query, len(results))
                return results
            logger.warning("SerpAPI %d for %r", resp.status_code, query)
    except Exception as e:
        logger.warning("SerpAPI failed for %r: %s", query, e)
    return []


# ── Yelp Fusion API ───────────────────────────────────────────────────────────

def _yelp_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.getenv('YELP_API_KEY', '')}"}


def _yelp_find_merchant(merchant: str, city: str) -> dict | None:
    """Find the merchant's own Yelp listing by name + city."""
    yelp_key = os.getenv("YELP_API_KEY", "")
    if not yelp_key:
        return None
    _delay()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                "https://api.yelp.com/v3/businesses/search",
                headers=_yelp_headers(),
                params={"term": merchant, "location": city, "limit": 1, "sort_by": "best_match"},
            )
            if resp.status_code == 200:
                businesses = resp.json().get("businesses", [])
                if businesses:
                    return businesses[0]
            else:
                logger.warning("Yelp merchant search %d for %r in %r", resp.status_code, merchant, city)
    except Exception as e:
        logger.warning("Yelp merchant lookup failed: %s", e)
    return None


def _yelp_scrape_reviews(yelp_url: str) -> list[dict[str, Any]]:
    """Yelp aggressively blocks all automated clients; returns empty — use _serp_review_quotes instead."""
    return []


def _serp_review_quotes(search_results: list[dict[str, Any]], merchant: str) -> list[dict[str, Any]]:
    """Extract customer review snippets from SerpAPI results for the reviews query."""
    quotes: list[dict[str, Any]] = []
    # Review-intent domains that reliably contain customer quotes in snippets
    review_domains = {"yelp.com", "tripadvisor.com", "google.com", "reddit.com",
                      "trustpilot.com", "bbb.org", "facebook.com", "carfax.com"}
    seen: set[str] = set()
    for r in search_results:
        snippet = r.get("snippet", "").strip()
        url = r.get("url", "")
        query = r.get("query", "")
        # Only use snippets from the reviews query, with meaningful text
        if "review" not in query.lower() or len(snippet) < 40:
            continue
        # Prefer review-site domains but accept any snippet with opinion signals
        domain = url.split("/")[2] if "//" in url else ""
        has_opinion = any(kw in snippet.lower() for kw in
                          ["i ", "we ", "my ", "great", "good", "bad", "love",
                           "recommend", "experience", "staff", "nice", "would", "felt"])
        # Always require opinion signals — exclude business listing metadata snippets
        if not has_opinion:
            continue
        if snippet in seen:
            continue
        seen.add(snippet)
        # Extract star rating from snippet if present ("4/5", "3.8 stars", "★")
        rating = None
        rm = re.search(r"(\d+\.?\d*)\s*(?:/5|stars?|★)", snippet, re.IGNORECASE)
        if rm:
            try:
                rating = float(rm.group(1))
                if rating > 5:
                    rating = None
            except ValueError:
                pass
        quotes.append({
            "text": snippet[:300],
            "rating": rating,
            "source": domain.replace("www.", ""),
            "url": url,
        })
        if len(quotes) >= 4:
            break
    return quotes


def _yelp_competitors(service_type: str, city: str, exclude_merchant: str) -> list[dict[str, Any]]:
    """Find up to 4 competitor businesses on Yelp in the same city/category."""
    yelp_key = os.getenv("YELP_API_KEY", "")
    if not yelp_key:
        return []
    _delay()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                "https://api.yelp.com/v3/businesses/search",
                headers=_yelp_headers(),
                params={"term": service_type, "location": city, "limit": 6, "sort_by": "best_match"},
            )
            if resp.status_code == 200:
                businesses = []
                for b in resp.json().get("businesses", []):
                    name = b.get("name", "")
                    if exclude_merchant.lower().split()[0] in name.lower():
                        continue
                    url = b.get("url", "")
                    price = b.get("price", "")
                    businesses.append({
                        "name": name,
                        "rating": b.get("rating"),
                        "review_count": b.get("review_count"),
                        "price_tier": price,
                        "url": url,
                        "source": "yelp",
                    })
                return businesses[:4]
            logger.warning("Yelp competitor search %d", resp.status_code)
    except Exception as e:
        logger.warning("Yelp competitor search failed: %s", e)
    return []


# ── Merchant direct price ─────────────────────────────────────────────────────

def _scrape_merchant_page(url: str) -> str:
    """Visit merchant website and extract price context."""
    if not url or "groupon.com" in url:
        return ""
    try:
        with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            time.sleep(random.uniform(1.0, 2.0))
            resp = client.get(url, timeout=20)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "lxml")
            text = soup.get_text(" ", strip=True)
            price_matches = re.findall(r"\$[\d,]+(?:\.\d{2})?", text)
            price_context = []
            for price in price_matches[:6]:
                idx = text.find(price)
                context = text[max(0, idx - 40):idx + 60].strip()
                price_context.append(context)
            return " | ".join(price_context[:3]) if price_context else ""
    except Exception as e:
        logger.warning("Merchant page scrape failed %s: %s", url, e)
        return ""


# ── Main research function ────────────────────────────────────────────────────

def research(audit: DealAudit) -> tuple[ResearchData, dict[str, Any]]:
    """Research competitive context for a deal. Returns (ResearchData, raw_data_dict)."""
    slug = audit.slug
    title = audit.title or slug
    merchant = audit.merchant_name or slug
    city = audit.city or "Chicago"
    category = audit.category or "spa"

    # Infer service type from title/category keywords
    service_type = category
    for kw in ["massage", "facial", "laser", "yoga", "oil change", "car wash",
                "kayak", "bowling", "spa", "detailing", "botox", "microblading",
                "colonic", "hydrotherapy", "manicure", "pedicure"]:
        if kw in title.lower() or kw in category.lower():
            service_type = kw
            break

    logger.info("Researching %s: service=%r city=%r merchant=%r", slug, service_type, city, merchant)

    all_search_results: list[dict] = []
    sources: list[str] = []

    # ── SerpAPI searches ──────────────────────────────────────────────────────
    queries = [
        f"{service_type} {city} price",
        f'"{merchant}" {city}',
        f"{service_type} {city} discount OR coupon",
        f"{merchant} {city} reviews",
    ]

    for q in queries:
        try:
            results = _serp_search(q, num_results=5)
            all_search_results.extend(results)
            sources.extend([r["url"] for r in results if r.get("url")])
        except Exception as e:
            logger.warning("Search query failed %r: %s", q, e)

    # Find merchant's own website from search results (not Groupon, not Yelp, not review sites)
    skip_domains = {"groupon.com", "yelp.com", "google.com", "facebook.com",
                    "tripadvisor.com", "yellowpages.com", "bbb.org"}
    merchant_website_url = ""
    merchant_name_token = merchant.lower().split()[0] if merchant else ""
    for r in all_search_results:
        url = r.get("url", "")
        if not url:
            continue
        domain = url.split("/")[2] if "//" in url else ""
        if any(d in domain for d in skip_domains):
            continue
        if merchant_name_token and merchant_name_token in domain.lower():
            merchant_website_url = url
            break

    merchant_direct_price = ""
    if merchant_website_url:
        logger.info("Checking merchant site: %s", merchant_website_url)
        merchant_direct_price = _scrape_merchant_page(merchant_website_url)

    # ── Yelp: merchant lookup + reviews ──────────────────────────────────────
    yelp_rating = None
    yelp_review_count = None
    review_quotes: list[dict] = []

    merchant_biz = _yelp_find_merchant(merchant, city)
    if merchant_biz:
        yelp_rating = merchant_biz.get("rating")
        yelp_review_count = merchant_biz.get("review_count")
        yelp_url = merchant_biz.get("url", "")
        if yelp_url:
            sources.append(yelp_url)
        logger.info(
            "Yelp merchant found: %s — %.1f★ (%d reviews)",
            merchant_biz.get("name", "?"),
            yelp_rating or 0,
            yelp_review_count or 0,
        )
        yelp_reviews = _yelp_scrape_reviews(yelp_url)
        review_quotes.extend(yelp_reviews)
        logger.info("Yelp reviews scraped: %d", len(yelp_reviews))
    else:
        logger.info("Yelp merchant not found for %r in %r", merchant, city)

    # ── Yelp: competitor businesses ───────────────────────────────────────────
    yelp_competitors = _yelp_competitors(service_type, city, merchant)
    logger.info("Yelp competitors found: %d", len(yelp_competitors))

    # ── Review quotes from SerpAPI snippets (TripAdvisor, Reddit, Google, etc.) ─
    serp_quotes = _serp_review_quotes(all_search_results, merchant)
    review_quotes.extend(serp_quotes)
    logger.info("SerpAPI review snippets extracted: %d", len(serp_quotes))

    # ── Groupon reviews as supplementary quotes ───────────────────────────────
    for rev in (audit.reviews or [])[:3]:
        if rev.text and len(rev.text) > 20:
            review_quotes.append({
                "text": rev.text[:300],
                "rating": rev.rating,
                "source": "groupon",
                "url": audit.url,
            })

    raw_data = {
        "search_results": all_search_results,
        "yelp_merchant": merchant_biz,
        "yelp_competitors": yelp_competitors,
        "yelp_rating": yelp_rating,
        "yelp_review_count": yelp_review_count,
        "review_quotes": review_quotes,
        "merchant_direct_price_raw": merchant_direct_price,
        "merchant_website_url": merchant_website_url,
        "sources": list(dict.fromkeys(sources)),  # deduplicate, preserve order
        "queries": queries,
        "slug": slug,
        "city": city,
        "category": category,
        "service_type": service_type,
        "merchant": merchant,
    }

    return ResearchData(
        url=audit.url,
        slug=slug,
        yelp_rating=yelp_rating,
        yelp_review_count=yelp_review_count,
        review_quotes=review_quotes,
        sources=raw_data["sources"],
        merchant_direct_price=merchant_direct_price or None,
        researched_at=datetime.now(timezone.utc).isoformat(),
    ), raw_data
