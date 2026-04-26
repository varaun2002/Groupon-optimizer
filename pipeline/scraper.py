from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from models.deal import (
    DealAudit,
    FAQ,
    ImageData,
    PricingOption,
    Review,
    SEOData,
    TrustSignals,
    UrgencyElements,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


def _city_from_url(url: str) -> str:
    path = urlparse(url).path
    parts = path.strip("/").split("/")
    # Try to find city in path like /deals/city-name-slug
    slug = parts[-1] if parts else ""
    # Common cities
    for city in ["chicago", "new-york", "los-angeles", "nyc", "la", "brooklyn", "midtown"]:
        if city in slug.lower():
            return city.replace("-", " ").title()
    return ""


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"\$?([\d,]+(?:\.\d{1,2})?)", text.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _extract_pricing(soup: BeautifulSoup) -> list[PricingOption]:
    options: list[PricingOption] = []

    # Try structured pricing blocks
    price_blocks = soup.select("[class*='price'], [class*='Price'], [data-testid*='price']")

    # Try JSON-LD for offers
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            offers = data.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for offer in offers:
                price = offer.get("price") or offer.get("lowPrice")
                name = offer.get("name", "Deal")
                opt = PricingOption(
                    name=str(name),
                    deal_price=float(price) if price else None,
                )
                options.append(opt)
        except Exception:
            pass

    if options:
        return options[:5]

    # Fallback: look for price text patterns
    text = soup.get_text(" ", strip=True)
    original_matches = re.findall(r"\$(\d+(?:\.\d{2})?)\s*(?:value|Value|original)", text)
    deal_matches = re.findall(r"(?:now|Now|for)\s*\$(\d+(?:\.\d{2})?)", text)

    if deal_matches:
        deal_price = float(deal_matches[0])
        orig_price = float(original_matches[0]) if original_matches else None
        discount = None
        savings = None
        if orig_price and deal_price:
            savings = orig_price - deal_price
            discount = round((savings / orig_price) * 100, 1)
        options.append(PricingOption(
            name="Deal",
            original_price=orig_price,
            deal_price=deal_price,
            discount_pct=discount,
            savings=savings,
        ))

    # Also look for discount percentage in page
    pct_matches = re.findall(r"(\d+)%\s*off", text, re.IGNORECASE)
    if pct_matches and not options:
        options.append(PricingOption(
            name="Deal",
            discount_pct=float(pct_matches[0]),
        ))

    return options[:5]


def _extract_highlights(soup: BeautifulSoup) -> list[str]:
    highlights: list[str] = []

    # Common selectors for highlights/what you get
    selectors = [
        "[class*='highlight']",
        "[class*='Highlight']",
        "[class*='option-description']",
        "[class*='deal-description']",
        "ul li",
    ]

    for sel in selectors:
        items = soup.select(sel)
        for item in items:
            text = item.get_text(strip=True)
            if 10 < len(text) < 300 and text not in highlights:
                highlights.append(text)
        if len(highlights) >= 3:
            break

    # Deduplicate and cap
    seen = set()
    unique = []
    for h in highlights:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique[:10]


def _extract_fine_print(soup: BeautifulSoup) -> list[str]:
    fine_print: list[str] = []
    selectors = [
        "[class*='fine-print']",
        "[class*='finePrint']",
        "[class*='restrictions']",
        "[class*='terms']",
        "[id*='fine-print']",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            for li in el.find_all("li"):
                text = li.get_text(strip=True)
                if text and text not in fine_print:
                    fine_print.append(text)
            if not fine_print:
                text = el.get_text(" ", strip=True)
                if text:
                    fine_print.append(text[:500])
    return fine_print[:10]


def _extract_faqs(soup: BeautifulSoup) -> list[FAQ]:
    faqs: list[FAQ] = []

    # Look for FAQ sections
    faq_section = soup.find(
        lambda tag: tag.name in ["section", "div", "ul"]
        and any(kw in (tag.get("class") or []) for kw in ["faq", "FAQ", "accordion"])
    )

    if not faq_section:
        # Try JSON-LD FAQPage schema
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "FAQPage":
                    for item in data.get("mainEntity", []):
                        q = item.get("name", "")
                        a = item.get("acceptedAnswer", {}).get("text", "")
                        if q:
                            faqs.append(FAQ(question=q, answer=a))
            except Exception:
                pass

    if faq_section:
        questions = faq_section.find_all(["dt", "summary", "h3", "h4"])
        for q_el in questions:
            q_text = q_el.get_text(strip=True)
            a_el = q_el.find_next_sibling(["dd", "div", "p"])
            a_text = a_el.get_text(strip=True) if a_el else ""
            if q_text:
                faqs.append(FAQ(question=q_text, answer=a_text))

    return faqs[:8]


def _extract_reviews(soup: BeautifulSoup) -> list[Review]:
    reviews: list[Review] = []
    review_selectors = [
        "[class*='review']",
        "[class*='Review']",
        "[itemprop='review']",
    ]
    for sel in review_selectors:
        for el in soup.select(sel)[:10]:
            text_el = el.find(class_=re.compile(r"text|body|comment", re.I))
            rating_el = el.find(attrs={"itemprop": "ratingValue"}) or el.find(
                class_=re.compile(r"rating|star", re.I)
            )
            author_el = el.find(attrs={"itemprop": "author"}) or el.find(
                class_=re.compile(r"author|name|user", re.I)
            )
            date_el = el.find(attrs={"itemprop": "datePublished"}) or el.find("time")

            text = text_el.get_text(strip=True) if text_el else el.get_text(strip=True)[:200]
            rating_str = rating_el.get("content") or rating_el.get_text(strip=True) if rating_el else None
            rating = None
            if rating_str:
                m = re.search(r"[\d.]+", rating_str)
                if m:
                    rating = float(m.group())

            if text and len(text) > 10:
                reviews.append(Review(
                    rating=rating,
                    text=text[:300],
                    author=author_el.get_text(strip=True)[:50] if author_el else None,
                    date=date_el.get("datetime") or date_el.get_text(strip=True)[:20] if date_el else None,
                ))
            if len(reviews) >= 5:
                break
        if reviews:
            break
    return reviews


def _extract_seo(soup: BeautifulSoup) -> SEOData:
    meta_title = None
    meta_desc = None

    title_tag = soup.find("title")
    if title_tag:
        meta_title = title_tag.get_text(strip=True)

    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag:
        meta_desc = desc_tag.get("content")

    h1 = None
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(strip=True)

    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")][:8]

    schema_types: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                t = data.get("@type")
                if t:
                    schema_types.append(t if isinstance(t, str) else str(t))
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        t = item.get("@type")
                        if t:
                            schema_types.append(t if isinstance(t, str) else str(t))
        except Exception:
            pass

    canonical = None
    can_tag = soup.find("link", attrs={"rel": "canonical"})
    if can_tag:
        canonical = can_tag.get("href")

    return SEOData(
        meta_title=meta_title,
        meta_description=meta_desc,
        h1=h1,
        h2s=h2s,
        schema_types=list(set(schema_types)),
        canonical_url=canonical,
    )


def _extract_trust_signals(soup: BeautifulSoup, text: str) -> TrustSignals:
    review_count = None
    avg_rating = None
    num_sold = None
    has_guarantee = False
    badges: list[str] = []

    # Rating patterns
    rating_match = re.search(r"(\d+\.?\d*)\s*(?:out of|/)\s*5", text)
    if rating_match:
        avg_rating = float(rating_match.group(1))

    # Review count
    count_match = re.search(r"([\d,]+)\s*(?:review|rating)", text, re.IGNORECASE)
    if count_match:
        review_count = int(count_match.group(1).replace(",", ""))

    # Sold count
    sold_match = re.search(r"([\d,]+\+?\s*(?:bought|sold|purchased))", text, re.IGNORECASE)
    if sold_match:
        num_sold = sold_match.group(1).strip()

    # Guarantee
    if re.search(r"guarantee|money.back|refund", text, re.IGNORECASE):
        has_guarantee = True

    # Badges from structured data
    badge_els = soup.select("[class*='badge'], [class*='Badge'], [class*='award']")
    for el in badge_els:
        b = el.get_text(strip=True)
        if b and len(b) < 50:
            badges.append(b)

    return TrustSignals(
        review_count=review_count,
        avg_rating=avg_rating,
        num_sold=num_sold,
        has_guarantee=has_guarantee,
        badges=badges[:5],
    )


def _extract_urgency(soup: BeautifulSoup, text: str) -> UrgencyElements:
    has_countdown = bool(soup.find(class_=re.compile(r"countdown|timer|clock", re.I)))
    limited_qty = None
    m = re.search(r"((?:only\s+)?\d+\s+(?:left|remaining|available))", text, re.IGNORECASE)
    if m:
        limited_qty = m.group(1)
    selling_fast = bool(re.search(r"selling fast|almost gone|high demand", text, re.IGNORECASE))
    return UrgencyElements(
        has_countdown=has_countdown,
        limited_quantity_text=limited_qty,
        selling_fast=selling_fast,
    )


def _parse_html(url: str, html: str) -> DealAudit:
    slug = _slug_from_url(url)
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    # Title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True).split("|")[0].strip() if title_tag else slug

    # Subtitle
    subtitle = None
    sub_el = soup.find(class_=re.compile(r"subtitle|sub-title|tagline", re.I))
    if sub_el:
        subtitle = sub_el.get_text(strip=True)[:200]

    # Merchant name — look in structured data first, then breadcrumbs, then page metadata
    merchant = ""
    # Try JSON-LD for seller/provider name
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for key in ("seller", "provider", "brand", "manufacturer"):
                val = data.get(key)
                if isinstance(val, dict):
                    name = val.get("name", "")
                    if name and len(name) < 80:
                        merchant = name
                        break
                elif isinstance(val, str) and len(val) < 80:
                    merchant = val
                    break
        except Exception:
            pass
        if merchant:
            break

    if not merchant:
        # Try structured merchant-specific selectors
        for sel in ["[class*='merchant-name']", "[class*='merchantName']",
                    "[class*='provider-name']", "[data-testid*='merchant']"]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t and 2 < len(t) < 80:
                    merchant = t
                    break

    if not merchant:
        # Try breadcrumb: last item before deal title
        breadcrumbs = soup.select("[class*='breadcrumb'] a, nav[aria-label*='breadcrumb'] a")
        for b in reversed(breadcrumbs):
            bt = b.get_text(strip=True)
            if bt and 2 < len(bt) < 60 and "groupon" not in bt.lower():
                merchant = bt
                break

    # City from URL slug or breadcrumb
    city = _city_from_url(url)
    if not city:
        breadcrumb = soup.select("[class*='breadcrumb'] a, nav a")
        for b in breadcrumb:
            bt = b.get_text(strip=True)
            if bt and len(bt) < 30:
                city = bt

    # Category from breadcrumb
    category = ""
    breadcrumbs = soup.select("[class*='breadcrumb'] a, nav a")
    if len(breadcrumbs) >= 2:
        category = breadcrumbs[-2].get_text(strip=True) if len(breadcrumbs) >= 2 else ""

    pricing = _extract_pricing(soup)
    highlights = _extract_highlights(soup)
    fine_print = _extract_fine_print(soup)
    faqs = _extract_faqs(soup)

    # Images in main area
    main = soup.find("main") or soup.find(id="main") or soup
    imgs = main.find_all("img") if main else soup.find_all("img")
    img_alts = [img.get("alt", "") for img in imgs if img.get("alt")]
    images = ImageData(count=len(imgs), alt_texts=img_alts[:20])

    script_count = len(soup.find_all("script"))
    stylesheet_count = len(soup.find_all("link", rel="stylesheet"))

    viewport_meta = None
    vp = soup.find("meta", attrs={"name": "viewport"})
    if vp:
        viewport_meta = vp.get("content")

    reviews = _extract_reviews(soup)
    seo = _extract_seo(soup)
    trust = _extract_trust_signals(soup, text)
    urgency = _extract_urgency(soup, text)

    return DealAudit(
        url=url,
        slug=slug,
        title=title,
        subtitle=subtitle,
        merchant_name=merchant,
        city=city,
        category=category,
        pricing_options=pricing,
        highlights=highlights,
        fine_print=fine_print,
        faqs=faqs,
        images=images,
        script_count=script_count,
        stylesheet_count=stylesheet_count,
        mobile_viewport_meta=viewport_meta,
        reviews=reviews,
        seo=seo,
        trust_signals=trust,
        urgency=urgency,
        raw_html_length=len(html),
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


async def _scrape_with_playwright(url: str) -> str:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
            )
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            content = await page.content()
            await browser.close()
            return content
    except Exception as e:
        logger.error("Playwright scrape failed for %s: %s", url, e)
        return ""


def scrape(url: str) -> DealAudit:
    delay = random.uniform(2.0, 3.5)
    logger.info("Sleeping %.1fs before scraping %s", delay, url)
    time.sleep(delay)

    html = ""
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
            resp = client.get(url)
            logger.info("httpx %s -> %d (%d bytes)", url, resp.status_code, len(resp.content))
            if resp.status_code == 200:
                html = resp.text
    except Exception as e:
        logger.warning("httpx failed for %s: %s", url, e)

    # Check if JS-rendered (no pricing found)
    needs_playwright = False
    if not html or "<title>" not in html:
        needs_playwright = True
    else:
        soup_check = BeautifulSoup(html, "lxml")
        page_text = soup_check.get_text(" ", strip=True)
        if len(page_text) < 500:
            needs_playwright = True

    if needs_playwright:
        logger.info("Falling back to playwright for %s", url)
        import asyncio
        try:
            html = asyncio.run(_scrape_with_playwright(url))
        except RuntimeError:
            import nest_asyncio
            nest_asyncio.apply()
            loop = asyncio.get_event_loop()
            html = loop.run_until_complete(_scrape_with_playwright(url))

    if not html:
        logger.error("Failed to get any HTML for %s", url)
        slug = _slug_from_url(url)
        return DealAudit(
            url=url,
            slug=slug,
            title=f"[Scrape Failed] {slug}",
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        audit = _parse_html(url, html)
        logger.info(
            "Scraped %s: title=%r pricing=%d highlights=%d",
            audit.slug, audit.title[:40], len(audit.pricing_options), len(audit.highlights),
        )
        return audit
    except Exception as e:
        logger.error("Parse error for %s: %s", url, e, exc_info=True)
        slug = _slug_from_url(url)
        return DealAudit(
            url=url,
            slug=slug,
            title=f"[Parse Failed] {slug}",
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )
