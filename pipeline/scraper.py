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
    m = re.search(r"\$?([\d,]+(?:\.\d{1,2})?)", text.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _extract_pricing(soup: BeautifulSoup) -> list[PricingOption]:
    options: list[PricingOption] = []

    # Primary: Groupon uses data-testid='purchase-single-option-{uuid}' for each pricing tier.
    # Within each block, sibling testids give us strike-through-price, green-price, discount.
    option_blocks = soup.find_all(
        attrs={"data-testid": re.compile(r"^purchase-single-option-")}
    )
    for block in option_blocks[:6]:
        # Option name: first substantial text before prices
        block_text = block.get_text(" ", strip=True)
        # Name = text up to the first "$" sign
        name_match = re.match(r"^(.+?)\s*\$", block_text)
        name = name_match.group(1).strip()[:120] if name_match else "Deal"

        orig_el = block.find(attrs={"data-testid": "strike-through-price"})
        sale_el = block.find(attrs={"data-testid": "limited-sale-price"})
        deal_el = block.find(attrs={"data-testid": "green-price"})
        disc_el = block.find(attrs={"data-testid": "discount"})
        promo_el = block.find(attrs={"data-testid": "promotion-price"})

        original_price = _parse_price(orig_el.get_text(strip=True) if orig_el else "")
        # limited-sale-price is the actual checkout price (lowest); green-price is the pre-sale deal price
        deal_price = _parse_price(sale_el.get_text(strip=True) if sale_el else "")
        if not deal_price:
            deal_price = _parse_price(deal_el.get_text(strip=True) if deal_el else "")
        discount_pct = None
        if disc_el:
            m = re.search(r"(\d+)", disc_el.get_text(strip=True))
            if m:
                discount_pct = float(m.group(1))

        # Coupon code: promotion-price element + sibling "with code XXXX" text
        coupon_price = _parse_price(promo_el.get_text(strip=True) if promo_el else "")
        coupon_code = None
        if promo_el:
            block_raw = block.get_text(" ", strip=True)
            code_m = re.search(r"with\s+code\s+([A-Z0-9]+)", block_raw, re.IGNORECASE)
            if code_m:
                coupon_code = code_m.group(1).upper()

        savings = None
        if original_price and deal_price:
            savings = round(original_price - deal_price, 2)
        if original_price and deal_price and discount_pct is None:
            discount_pct = round((savings / original_price) * 100, 1)

        if deal_price or original_price:
            options.append(PricingOption(
                name=name,
                original_price=original_price,
                deal_price=deal_price,
                discount_pct=discount_pct,
                savings=savings,
                coupon_code=coupon_code,
                coupon_price=coupon_price,
            ))

    if options:
        logger.info("Extracted %d pricing options via data-testid", len(options))
        return options

    # Fallback: parse from meta title "Merchant - From $XX.XX - City | Groupon"
    title_tag = soup.find("title")
    if title_tag:
        m = re.search(r"From \$([\d,]+(?:\.\d{2})?)", title_tag.get_text())
        if m:
            deal_price = float(m.group(1).replace(",", ""))
            options.append(PricingOption(name="Deal", deal_price=deal_price))
            logger.info("Extracted deal price from meta title: $%s", deal_price)
            return options

    # Last resort: discount % from page text
    text = soup.get_text(" ", strip=True)
    pct_matches = re.findall(r"(\d+)%\s*off", text, re.IGNORECASE)
    if pct_matches:
        options.append(PricingOption(name="Deal", discount_pct=float(pct_matches[0])))

    return options[:6]


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

    # Groupon places fine print in plain text after "Terms & Conditions" heading.
    # No dedicated CSS class exists — parse from full page text.
    full_text = soup.get_text(" ", strip=True)
    m = re.search(
        r"Terms\s*&\s*Conditions\s+(.*?)(?:Legal Disclosures|Show More|Groupon is not|$)",
        full_text, re.DOTALL | re.IGNORECASE,
    )
    if m:
        block = m.group(1).strip()
        # Split into individual rules on ". " sentence boundaries
        sentences = re.split(r"(?<=\.)\s+(?=[A-Z])", block)
        for s in sentences:
            s = s.strip()
            if s and len(s) > 10:
                fine_print.append(s)

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

    # Groupon uses data-testid='customer-review' for each review block.
    for el in soup.find_all(attrs={"data-testid": "customer-review"})[:5]:
        raw = el.get_text(" ", strip=True)

        # Author is the first word(s) before "ratings|reviews|days ago"
        author_m = re.match(r"^([A-Za-z][A-Za-z\s]{1,30}?)(?:\d|\s*Top)", raw)
        author = author_m.group(1).strip() if author_m else None

        # Date: "X days ago" / "X months ago"
        date_m = re.search(r"(\d+\s+(?:day|days|month|months|year|years)\s+ago)", raw, re.IGNORECASE)
        date = date_m.group(1) if date_m else None

        # Review text: everything after the date
        text = raw
        if date:
            idx = raw.find(date)
            text = raw[idx + len(date):].strip()
        elif author:
            # Strip author + rating meta prefix
            text = re.sub(r"^\S+\s+\d+\s+ratings?.*?\d+\s+reviews?\s*", "", raw, flags=re.IGNORECASE).strip()

        if text and len(text) > 5:
            reviews.append(Review(
                rating=None,  # individual star not shown per review in DOM
                text=text[:400],
                author=author,
                date=date,
            ))

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

    # Groupon exposes overall rating via data-testid='rating'
    rating_el = soup.find(attrs={"data-testid": "rating"})
    if rating_el:
        try:
            avg_rating = float(rating_el.get_text(strip=True))
        except ValueError:
            pass

    def _parse_count(raw: str) -> int | None:
        m = re.search(r"([\d.]+)\s*([KkMm]?)\+?", raw.replace(",", ""))
        if not m:
            return None
        n = float(m.group(1))
        suffix = m.group(2).upper()
        if suffix == "K":
            n *= 1000
        elif suffix == "M":
            n *= 1_000_000
        return int(n)

    # Review count: look for "X ratings" / "8K+ ratings" near the rating widget, then fall back to regex
    if avg_rating is not None:
        rating_ctx = rating_el.parent.get_text(" ", strip=True) if rating_el and rating_el.parent else ""
        m = re.search(r"([\d.,]+[KkMm]?\+?)\s*ratings?", rating_ctx, re.IGNORECASE)
        if m:
            review_count = _parse_count(m.group(1))

    if review_count is None:
        m = re.search(r"([\d.,]+[KkMm]?\+?)\s*(?:ratings?|reviews?)", text, re.IGNORECASE)
        if m:
            review_count = _parse_count(m.group(1))

    # Sold count
    sold_match = re.search(r"([\d,]+\+?\s*(?:bought|sold|purchased))", text, re.IGNORECASE)
    if sold_match:
        num_sold = sold_match.group(1).strip()

    # Guarantee
    if re.search(r"guarantee|money.back|refund", text, re.IGNORECASE):
        has_guarantee = True

    # Badges: data-testid='desktop-deal-badges'
    badge_el = soup.find(attrs={"data-testid": "desktop-deal-badges"})
    if badge_el:
        raw = badge_el.get_text(" ", strip=True)
        # Split on capitals (e.g. "Best RatedPopular Gift" → ["Best Rated", "Popular Gift"])
        parts = re.findall(r"[A-Z][a-z]+(?:\s+[A-Za-z]+)*", raw)
        badges = [p.strip() for p in parts if p.strip()][:5]

    return TrustSignals(
        review_count=review_count,
        avg_rating=avg_rating,
        num_sold=num_sold,
        has_guarantee=has_guarantee,
        badges=badges,
    )


def _extract_urgency(soup: BeautifulSoup, text: str) -> UrgencyElements:
    # data-testid='sale-countdown' is the live timer element
    has_countdown = bool(soup.find(attrs={"data-testid": "sale-countdown"}))

    # Limited sale label: "Extra $X off, today only" or "Limited time"
    limited_qty = None
    label_els = soup.find_all(attrs={"data-testid": "limited-sale-label"})
    if label_els:
        limited_qty = label_els[0].get_text(strip=True)

    # "Selling Fast" badge
    selling_fast = bool(
        soup.find(attrs={"data-testid": re.compile(r"selling.fast", re.I)})
        or re.search(r"selling fast|almost gone|high demand", text, re.IGNORECASE)
    )
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

    # Merchant name — most reliable source is meta title: "Merchant - From $XX - City | Groupon"
    merchant = ""
    title_tag = soup.find("title")
    if title_tag:
        parts = title_tag.get_text(strip=True).split(" - ")
        if parts and len(parts[0]) > 2:
            merchant = parts[0].strip()[:80]

    # Fallback: "at MerchantName" pattern in H1
    if not merchant and h1:
        at_m = re.search(r"\bat\s+([A-Z][A-Za-z0-9\s&']+?)(?:\s*[\(:]|$)", h1.get_text(strip=True))
        if at_m:
            merchant = at_m.group(1).strip()[:80]

    # City — parse from data-testid='dealLocationsList' address text, or meta title fallback.
    # Meta title format: "Merchant Name - From $XX - City | Groupon"
    city = ""
    loc_el = soup.find(attrs={"data-testid": "dealLocationsList"})
    if loc_el:
        loc_text = loc_el.get_text(" ", strip=True)
        # Address usually contains "City" — extract last city-like token before state abbreviation
        city_m = re.search(r",\s*([A-Za-z\s]+),\s*[A-Z]{2}\b", loc_text)
        if city_m:
            city = city_m.group(1).strip()
    if not city:
        title_tag = soup.find("title")
        if title_tag:
            # "Merchant - From $XX - Chicago | Groupon"
            city_m = re.search(r"-\s*([A-Za-z\s]+)\s*\|\s*Groupon", title_tag.get_text())
            if city_m:
                city = city_m.group(1).strip()
    if not city:
        city = _city_from_url(url)
    # Final fallback: scan full page text for "Street, City, ST ZIP" address patterns
    if not city:
        addr_m = re.search(r",\s*([A-Za-z][A-Za-z\s]{2,20}),\s*[A-Z]{2}\s+\d{5}", text)
        if addr_m:
            city = addr_m.group(1).strip()

    # Category — last breadcrumb link (most specific), excluding "Groupon" / "Local"
    category = ""
    bc_texts = [
        bc.get_text(strip=True)
        for bc in soup.find_all("a", attrs={"data-testid": re.compile(r"breadcrumb", re.I)})
        if bc.get_text(strip=True) and len(bc.get_text(strip=True)) < 50
        and "groupon" not in bc.get_text(strip=True).lower()
    ]
    if bc_texts:
        category = bc_texts[-1]
    if not category:
        bcs = soup.select("nav a, [aria-label*='breadcrumb'] a")
        candidates = [b.get_text(strip=True) for b in bcs if 2 < len(b.get_text(strip=True)) < 50]
        if candidates:
            category = candidates[-1]

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
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception:
                # networkidle timed out — retry with domcontentloaded + extra wait
                logger.info("networkidle timeout, retrying with domcontentloaded for %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(6000)
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
