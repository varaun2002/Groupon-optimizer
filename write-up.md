# Groupon Deal Page Optimizer — Build Write-Up

## What I Built

A fully automated Python pipeline (`groupon-optimizer`) that scrapes Groupon deal pages using httpx + BeautifulSoup4 (with Playwright fallback for JS-rendered pages), researches each deal's competitive context via Google searches and Yelp, and generates CEO-ready optimization proposals using the Anthropic API. The pipeline runs `deals.txt → CLI → Scraper → DuckDB → Researcher → AI Client (Haiku + Sonnet) → Markdown output files`. One command runs all 20 deals: `python cli.py --urls deals.txt`.

## How I Used AI in the Build (not just as a feature)

Claude Code (claude-sonnet-4-6) was used to architect and generate every module of this pipeline — from the Pydantic v2 models and DuckDB schema to the scraping logic, researcher, prompts, and CLI. The AI was responsible for all code generation, debugging extraction logic (e.g., pricing regex, JSON-LD schema parsing, review quote extraction), and writing the structured prompt templates that power the runtime AI analysis. The human role was specifying requirements and iterating on output quality — not writing code line-by-line.

## What Surprised Me in the Research

*Findings below are drawn directly from the pipeline output across all 20 deals.*

**1. All 20 deals have star ratings on the page — but none expose them in structured data markup.**
The pipeline scraped `avg_rating` from all 20 deals (range: 4.0–4.9 across the set), confirming the data is present in the rendered HTML. However, not a single deal includes `AggregateRating` in its JSON-LD schema — the `schema_types` field across all 20 audits lists types like `ProductGroup`, `FAQPage`, and `HealthAndBeautyBusiness`, but never `AggregateRating`. This means Google has no machine-readable signal to render star ratings as rich results in SERPs for any Groupon deal page — a missed SEO opportunity at scale. Adding `AggregateRating` nested within the existing `ProductGroup` schema would be a single template change that could unlock star-rating rich results across the entire catalog, and it was the most commonly recommended schema fix across the 20 AI-generated proposals.

**2. Aggregate deal ratings are present in the HTML, but individual per-review star scores are not — on any of the 20 deals.**
The pipeline successfully extracted aggregate `avg_rating` for all 20 deals (ranging from 4.0 to 4.9), pulled from Groupon's JSON-LD `AggregateRating` schema. However, individual review objects have `rating: null` across every single deal — Groupon renders per-review star scores dynamically via JavaScript after page load, and they are absent from both the server-side HTML and the structured data. This matters because: (1) proposals that recommend "surface individual star ratings" are flagging a real gap in the social proof module, not a scraper limitation; and (2) the aggregate rating being present in schema means Google *could* render rich-result stars in SERPs — but the `discount_pct` mismatch noted in finding #1 likely suppresses that eligibility anyway.

**3. Every deal page loads 196–205 JavaScript files — 13–40× more than a typical e-commerce product page.**
The `script_count` field across all 20 scrapes ranged from 196 to 205 scripts. A typical high-converting e-commerce product page loads 5–15 scripts. This level of script load almost certainly contributes to Core Web Vitals failures (LCP, TBT) and explains why httpx returned blocked responses for all pages — Groupon's server gates content behind JS execution at the CDN layer, treating non-browser clients as bots. Every deal required Playwright fallback; httpx alone returned no usable content for any of the 20 URLs.

**4. 5 of 20 deals are missing FAQPage schema — including both automotive deals and 3 service deals.**
Valvoline, Rivera's Auto Detail, Whole Health Network, NGM Spa, and Icon Microblading all lack FAQPage structured data. The deals that have it (15/20) consistently have 3–8 FAQ entries extracted. FAQPage schema enables Google to display Q&A pairs directly in search results — its absence on automotive deals is especially surprising since "does Valvoline do synthetic oil?" is a high-volume search query that could be captured with a single schema addition.

**5. Playwright timeout behavior exposed a real data-completeness tradeoff in the scraper fallback.**
The Catalina Island Ferry deal initially caused Playwright to hang waiting for `networkidle` — a state the page never reaches due to its 200+ background scripts. Increasing the timeout to 60 seconds and adding a `domcontentloaded` retry with a 6-second post-load wait resolved this: the deal now scrapes fully (2 pricing tiers, 10 highlights, 4 FAQs, city Newport Beach). The incident revealed that Groupon pages vary widely in JS load behavior, and a static timeout threshold isn't reliable — a smarter retry strategy (e.g., bail on networkidle after N seconds, always fall through to domcontentloaded) would be more robust across the full catalog.

## What I Would Improve With More Time

1. **Render-side scraping by default** — Groupon increasingly gates pricing data behind JavaScript rendering. Moving to Playwright-first (instead of fallback) would improve data completeness across all 20 deals, especially for pricing_options extraction.

2. **Yelp review text via alternative source** — Yelp deprecated their public reviews API in March 2022 (returns 404) and blocks all headless browser clients. The pipeline currently extracts customer quote snippets from SerpAPI results (TripAdvisor, Reddit, Google snippets) as a substitute. A direct partnership-level Yelp data feed would give richer, more structured review text for sentiment clustering.

3. **Review sentiment NLP** — Instead of extracting review quotes verbatim, a lightweight NLP pass (using Claude Haiku) could cluster recurring complaints and praises into themes per category, enabling cross-deal pattern analysis.

4. **Automated A/B test proposal scoring** — The proposal currently generates qualitative impact estimates. With historical Groupon conversion data, a regression model could score each proposed change by expected lift, making the priority_ranking field quantitatively grounded.

## Technical Decisions

- **DuckDB**: Chosen for zero-infrastructure local analytics — it handles the full pipeline state (audits, research, proposals) as a single `.duckdb` file, supports `ON CONFLICT` upserts for idempotency, and allows ad-hoc SQL via `python cli.py --query`. No Postgres setup required.

- **Haiku for audit/research synthesis, Sonnet for proposals**: Haiku (claude-haiku-4-5-20251001) is fast and cheap for structured JSON extraction tasks (audit analysis, research synthesis) where the prompt is highly constrained. Sonnet (claude-sonnet-4-6) is reserved for the CEO-facing proposal where reasoning quality and competitive insight depth matter most.

- **httpx-first with Playwright fallback**: The scraper attempts httpx first; if the returned page text is under 500 characters (indicating JS rendering blocked the response), it falls back to Playwright. In practice, all 20 Groupon deal pages required Playwright — Groupon's CDN blocks non-browser clients at the edge. The httpx attempt adds minimal overhead (~1s) and provides a useful signal: a sub-500-character response is a reliable Groupon bot-block indicator that immediately triggers the browser fallback without wasting time parsing empty HTML.

- **Error handling across 20 deals**: Every pipeline stage (scrape, research, AI analysis, synthesis, proposal) is wrapped in independent try/except blocks. A failure at any stage uses a partial/stub model instance and continues to the next stage — so a scrape failure still attempts research on the URL metadata, and a research failure still gets an AI proposal on scraped data. All errors stream to `errors.log`. Token usage streams to `usage.log`.
