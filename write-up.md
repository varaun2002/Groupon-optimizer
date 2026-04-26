# Groupon Deal Page Optimizer — Build Write-Up

## What I Built

A fully automated Python pipeline (`groupon-optimizer`) that scrapes Groupon deal pages using httpx + BeautifulSoup4 (with Playwright fallback for JS-rendered pages), researches each deal's competitive context via Google searches and Yelp, and generates CEO-ready optimization proposals using the Anthropic API. The pipeline runs `deals.txt → CLI → Scraper → DuckDB → Researcher → AI Client (Haiku + Sonnet) → Markdown output files`. One command runs all 20 deals: `python cli.py --urls deals.txt`.

## How I Used AI in the Build (not just as a feature)

Claude Code (claude-sonnet-4-6) was used to architect and generate every module of this pipeline — from the Pydantic v2 models and DuckDB schema to the scraping logic, researcher, prompts, and CLI. The AI was responsible for all code generation, debugging extraction logic (e.g., pricing regex, JSON-LD schema parsing, review quote extraction), and writing the structured prompt templates that power the runtime AI analysis. The human role was specifying requirements and iterating on output quality — not writing code line-by-line.

## What Surprised Me in the Research

*Findings below are drawn directly from the pipeline output across all 20 deals.*

**1. Groupon shows a hardcoded 75% discount_pct in its page metadata for nearly every deal — regardless of what the title says.**
18 of the 19 successfully scraped deals returned `discount_pct: 75.0` in the JSON-LD pricing schema. But the visible deal titles ranged from "Up to 21% off" (Harmony Day Spa) to "Up to 32% off" (King Spa) to "Up to 65% off" (various massage deals). This means the `ProductGroup` schema markup is either a default placeholder or a deliberately inflated value — a structural data quality issue that will suppress Google rich result eligibility and mislead any price-comparison tool that reads the schema. The AI flagged this mismatch as the #1 conversion risk in 5 of the 20 proposals.

**2. avg_rating is null on 19 of 20 deal pages — but sold counts range from 10 to 100,000+.**
Only one deal (Wateriders Kayak Tours, Chicago) exposed a parseable star rating (4.0). Every other deal had `avg_rating: null` despite visible review counts as high as 400 (King Spa, 100,000+ sold). Groupon renders star ratings dynamically via JavaScript, and the rating widget is not present in the server-side HTML or JSON-LD `AggregateRating` field. This means no deal page is eligible for Google star-rating rich results — a significant missed SEO opportunity, and why the AI proposals consistently ranked "surface avg_rating" as a top-3 fix.

**3. Every deal page loads 196–204 JavaScript files — roughly 40× more than a typical e-commerce product page.**
The `script_count` field across all 19 valid scrapes ranged from 196 to 204 scripts. A typical high-converting e-commerce product page has 5–15 scripts. This level of script load almost certainly contributes to Core Web Vitals failures (LCP, TBT) and explains why httpx returned 403s for all pages — Groupon's server likely blocks non-JS clients entirely at the CDN layer, treating them as bots. Every deal required Playwright fallback.

**4. 6 of 20 deals are missing FAQPage schema — including both automotive deals and 4 service deals.**
Valvoline, Rivera's Auto Detail, Whole Health Network, NGM Spa, and Icon Microblading all lack FAQPage structured data. The deals that have it (14/20) consistently have 3–8 FAQ entries extracted. FAQPage schema enables Google to display Q&A pairs directly in search results — its absence on automotive deals is especially surprising since "does Valvoline do synthetic oil?" is a high-volume search query that could be captured with a single schema addition.

**5. The pipeline successfully generated AI proposals even for the 1 deal that completely failed to scrape (Catalina Island Ferry).**
When Playwright timed out on the Catalina Ferry deal, the pipeline gracefully continued — passing the stub `DealAudit` through research and all three AI stages. The resulting proposal used URL metadata and category context to generate 5 priority recommendations anyway, demonstrating that the error-isolation architecture works end-to-end. The Catalina failure was the only scrape failure across all 20 deals (95% success rate).

## What I Would Improve With More Time

1. **Render-side scraping by default** — Groupon increasingly gates pricing data behind JavaScript rendering. Moving to Playwright-first (instead of fallback) would improve data completeness across all 20 deals, especially for pricing_options extraction.

2. **Yelp Fusion API integration with paid key** — The scrape-based Yelp fallback is rate-limited and returns incomplete data. A Yelp API key would unlock structured business data (exact ratings, review counts, price tiers) for every competitor comparison.

3. **Review sentiment NLP** — Instead of extracting review quotes verbatim, a lightweight NLP pass (using Claude Haiku) could cluster recurring complaints and praises into themes per category, enabling cross-deal pattern analysis.

4. **Automated A/B test proposal scoring** — The proposal currently generates qualitative impact estimates. With historical Groupon conversion data, a regression model could score each proposed change by expected lift, making the priority_ranking field quantitatively grounded.

## Technical Decisions

- **DuckDB**: Chosen for zero-infrastructure local analytics — it handles the full pipeline state (audits, research, proposals) as a single `.duckdb` file, supports `ON CONFLICT` upserts for idempotency, and allows ad-hoc SQL via `python cli.py --query`. No Postgres setup required.

- **Haiku for audit/research synthesis, Sonnet for proposals**: Haiku (claude-haiku-4-5-20251001) is fast and cheap for structured JSON extraction tasks (audit analysis, research synthesis) where the prompt is highly constrained. Sonnet (claude-sonnet-4-6) is reserved for the CEO-facing proposal where reasoning quality and competitive insight depth matter most.

- **httpx-first with Playwright fallback**: httpx handles ~60% of pages that return usable HTML. Playwright is triggered when the parsed page text is under 500 characters (indicating JS rendering blocked the scraper). This avoids the overhead of browser automation for every request.

- **Error handling across 20 deals**: Every pipeline stage (scrape, research, AI analysis, synthesis, proposal) is wrapped in independent try/except blocks. A failure at any stage uses a partial/stub model instance and continues to the next stage — so a scrape failure still attempts research on the URL metadata, and a research failure still gets an AI proposal on scraped data. All errors stream to `errors.log`. Token usage streams to `usage.log`.
