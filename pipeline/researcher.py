from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, unquote, parse_qs, urlparse

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
    time.sleep(random.uniform(2.0, 3.5))


def _safe_fetch(url: str, client: httpx.Client) -> str:
    _delay()
    try:
        resp = client.get(url, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        logger.warning("Fetch %s -> %d", url, resp.status_code)
    except Exception as e:
        logger.warning("Fetch failed %s: %s", url, e)
    return ""


def _decode_ddg_url(href: str) -> str:
    """Extract real URL from DuckDuckGo redirect href."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    # DuckDuckGo uses 'uddg' param for the real URL
    if "uddg" in qs:
        return unquote(qs["uddg"][0])
    return href


def _ddg_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    encoded = quote_plus(query)
    ddg_url = f"https://html.duckduckgo.com/html/?q={encoded}"

    for attempt in range(3):
        if attempt > 0:
            wait = 4 + attempt * 2
            logger.info("DDG retry %d for %r, sleeping %ds", attempt, query, wait)
            time.sleep(wait)
        try:
            with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as client:
                resp = client.get(ddg_url)
                if resp.status_code == 202:
                    logger.warning("DDG returned 202 (rate limit) for %r attempt %d", query, attempt)
                    continue
                if resp.status_code != 200:
                    logger.warning("DDG %d for %r attempt %d", resp.status_code, query, attempt)
                    continue
                html = resp.text
                soup = BeautifulSoup(html, "lxml")
                for a in soup.select(".result__a")[:num_results]:
                    href = a.get("href", "")
                    real_url = _decode_ddg_url(href) if href else ""
                    title = a.get_text(strip=True)
                    if real_url and "duckduckgo.com" not in real_url:
                        results.append({"url": real_url, "query": query, "title": title})
                if results:
                    break
        except Exception as e:
            logger.warning("DuckDuckGo search failed for %r attempt %d: %s", query, attempt, e)
    return results


def _google_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    # Primary: DuckDuckGo (more reliable without API key)
    results = _ddg_search(query, num_results)
    if results:
        return results
    # Fallback: googlesearch-python
    try:
        from googlesearch import search
        _delay()
        for url in search(query, num_results=num_results, lang="en"):
            results.append({"url": url, "query": query})
    except Exception as e:
        logger.warning("Google search fallback also failed for %r: %s", query, e)
    return results


def _yelp_api_search(category: str, city: str) -> list[dict[str, Any]]:
    yelp_key = os.getenv("YELP_API_KEY", "")
    if not yelp_key:
        return []
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                "https://api.yelp.com/v3/businesses/search",
                headers={"Authorization": f"Bearer {yelp_key}"},
                params={"term": category, "location": city, "limit": 5},
            )
            if resp.status_code == 200:
                data = resp.json()
                businesses = []
                for b in data.get("businesses", []):
                    businesses.append({
                        "name": b.get("name", ""),
                        "rating": b.get("rating"),
                        "review_count": b.get("review_count"),
                        "price": b.get("price", ""),
                        "url": b.get("url", ""),
                    })
                return businesses
    except Exception as e:
        logger.warning("Yelp API error: %s", e)
    return []


def _scrape_yelp_search(category: str, city: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            url = f"https://www.yelp.com/search?find_desc={quote_plus(category)}&find_loc={quote_plus(city)}"
            html = _safe_fetch(url, client)
            if not html:
                return results
            soup = BeautifulSoup(html, "lxml")
            for biz in soup.select("[class*='businessName'], h3 a")[:5]:
                name = biz.get_text(strip=True)
                link = biz.get("href", "")
                if name and len(name) < 80:
                    results.append({
                        "name": name,
                        "url": f"https://www.yelp.com{link}" if link.startswith("/") else link,
                        "source": "yelp_search",
                    })
    except Exception as e:
        logger.warning("Yelp scrape failed: %s", e)
    return results


def _scrape_merchant_page(url: str) -> str:
    """Visit merchant website and extract price info."""
    if not url or "groupon.com" in url:
        return ""
    try:
        with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            html = _safe_fetch(url, client)
            if not html:
                return ""
            soup = BeautifulSoup(html, "lxml")
            text = soup.get_text(" ", strip=True)
            # Look for price mentions
            price_matches = re.findall(r"\$[\d,]+(?:\.\d{2})?", text)
            price_context = []
            for price in price_matches[:5]:
                idx = text.find(price)
                context = text[max(0, idx - 30):idx + 50].strip()
                price_context.append(context)
            return " | ".join(price_context[:3]) if price_context else ""
    except Exception as e:
        logger.warning("Merchant page scrape failed %s: %s", url, e)
        return ""


def _extract_snippets(search_results: list[dict]) -> str:
    """Build a text blob from search results for AI synthesis."""
    lines = []
    for r in search_results:
        url = r.get("url", "")
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        if url:
            lines.append(f"URL: {url}")
        if title:
            lines.append(f"Title: {title}")
        if snippet:
            lines.append(f"Snippet: {snippet}")
        lines.append("")
    return "\n".join(lines)


def research(audit: DealAudit) -> tuple[ResearchData, dict[str, Any]]:
    """Research competitive context for a deal. Returns (ResearchData, raw_data_dict)."""
    slug = audit.slug
    title = audit.title or slug
    merchant = audit.merchant_name or slug
    city = audit.city or "Chicago"
    category = audit.category or "spa"

    # Infer service type from title/category
    service_type = category
    for kw in ["massage", "facial", "laser", "yoga", "oil change", "car wash",
                "kayak", "bowling", "spa", "detailing", "botox"]:
        if kw in title.lower() or kw in category.lower():
            service_type = kw
            break

    logger.info("Researching %s: service=%r city=%r merchant=%r", slug, service_type, city, merchant)

    all_search_results: list[dict] = []
    sources: list[str] = []

    queries = [
        f"{service_type} {city} price",
        f"{merchant} {city} reviews",
        f"{service_type} {city} coupon OR discount",
        f"{merchant} website booking",
    ]

    for q in queries:
        try:
            results = _google_search(q, num_results=5)
            all_search_results.extend(results)
            sources.extend([r["url"] for r in results if r.get("url")])
            logger.info("Query %r -> %d results", q, len(results))
        except Exception as e:
            logger.warning("Search query failed %r: %s", q, e)

    # Yelp competitors
    yelp_businesses: list[dict] = []
    yelp_api_results = _yelp_api_search(category, city)
    if yelp_api_results:
        yelp_businesses = yelp_api_results
        logger.info("Yelp API: %d businesses", len(yelp_businesses))
    else:
        yelp_businesses = _scrape_yelp_search(service_type, city)
        logger.info("Yelp scrape: %d businesses", len(yelp_businesses))

    # Merchant direct price — find their website from search results
    merchant_direct_price = ""
    merchant_website_url = ""
    for r in all_search_results:
        url = r.get("url", "")
        if url and "groupon.com" not in url and "yelp.com" not in url and merchant.lower().split()[0] in url.lower():
            merchant_website_url = url
            break

    if merchant_website_url:
        logger.info("Checking merchant site: %s", merchant_website_url)
        merchant_direct_price = _scrape_merchant_page(merchant_website_url)

    # Yelp merchant rating — search for merchant specifically
    yelp_rating = None
    yelp_review_count = None
    review_quotes: list[dict] = []

    merchant_yelp_results = _google_search(f"{merchant} {city} site:yelp.com", num_results=3)
    yelp_url = None
    for r in merchant_yelp_results:
        url = r.get("url", "")
        if "yelp.com/biz/" in url:
            yelp_url = url
            sources.append(url)
            break

    if yelp_url:
        try:
            with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
                html = _safe_fetch(yelp_url, client)
                if html:
                    soup = BeautifulSoup(html, "lxml")
                    text = soup.get_text(" ", strip=True)

                    # Rating
                    rating_m = re.search(r"(\d+\.?\d*)\s*star rating", text, re.IGNORECASE)
                    if not rating_m:
                        rating_m = re.search(r'"ratingValue"\s*:\s*"?([\d.]+)"?', html)
                    if rating_m:
                        try:
                            yelp_rating = float(rating_m.group(1))
                        except ValueError:
                            pass

                    # Review count
                    count_m = re.search(r"([\d,]+)\s*review", text, re.IGNORECASE)
                    if count_m:
                        try:
                            yelp_review_count = int(count_m.group(1).replace(",", ""))
                        except ValueError:
                            pass

                    # Review quotes
                    review_els = soup.select("[class*='review'] p, [class*='comment'] p")[:5]
                    for el in review_els:
                        qt = el.get_text(strip=True)
                        if len(qt) > 20:
                            review_quotes.append({
                                "text": qt[:300],
                                "rating": None,
                                "source": "yelp",
                                "url": yelp_url,
                            })
        except Exception as e:
            logger.warning("Yelp page scrape failed: %s", e)

    raw_data = {
        "search_results": all_search_results,
        "yelp_businesses": yelp_businesses,
        "yelp_rating": yelp_rating,
        "yelp_review_count": yelp_review_count,
        "review_quotes": review_quotes,
        "merchant_direct_price_raw": merchant_direct_price,
        "merchant_website_url": merchant_website_url,
        "sources": list(set(sources)),
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
        sources=list(set(sources)),
        merchant_direct_price=merchant_direct_price or None,
        researched_at=datetime.now(timezone.utc).isoformat(),
    ), raw_data
