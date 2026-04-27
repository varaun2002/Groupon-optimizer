# Groupon Deal Page Optimizer

AI-powered pipeline that audits Groupon deal pages, researches competitive context, and generates actionable optimization proposals backed by real data.

## Setup

```bash
git clone <repo>
cd groupon-optimizer
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Add your ANTHROPIC_API_KEY (and optionally YELP_API_KEY) to .env
```

## Run

```bash
# Process all 20 deals
python cli.py --urls deals.txt

# Process one deal
python cli.py --url https://www.groupon.com/deals/king-spa-and-sauna-chicago-35

# Resume interrupted run (skip already-completed deals)
python cli.py --urls deals.txt --resume

# Query the database
python cli.py --query "SELECT slug, city, category, avg_rating FROM audits ORDER BY avg_rating ASC"
python cli.py --query "SELECT slug, generated_at FROM proposals"
```

## Output Structure

```
output/
└── {deal-slug}/
    ├── audit.json          # Full scraped deal data (all DealAudit fields)
    ├── audit_summary.md    # Human-readable audit + AI scores & weaknesses
    ├── research.md         # Competitive research with citations and quotes
    └── proposal.md         # CEO-ready optimization proposal with priority ranking
```

## Architecture

```
deals.txt
    │
    ▼
CLI (cli.py)
    │
    ├─► Scraper (pipeline/scraper.py)
    │       httpx + BeautifulSoup4
    │       Playwright fallback (JS-rendered pages)
    │       └─► DealAudit → DuckDB (audits table)
    │
    ├─► Researcher (pipeline/researcher.py)
    │       SerpAPI (real Google results — competitor prices, review snippets)
    │       Yelp Fusion API (merchant ratings, competitor businesses)
    │       Merchant website visit (direct price scrape)
    │       └─► Raw data dict
    │
    ├─► AI Client (pipeline/ai/client.py)
    │       analyze_audit()    → claude-haiku-4-5-20251001
    │       synthesize_research() → claude-haiku-4-5-20251001 → ResearchData → DuckDB
    │       generate_proposal() → claude-sonnet-4-6 → OptimizationProposal → DuckDB
    │
    └─► Output Writer (cli.py:_write_outputs)
            output/{slug}/audit.json
            output/{slug}/audit_summary.md
            output/{slug}/research.md
            output/{slug}/proposal.md
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (sk-ant-...) |
| `YELP_API_KEY` | No | Yelp Fusion API key — merchant ratings and competitor data; skipped if absent |
| `SERP_API_KEY` | No | SerpAPI key — real Google search results for competitor pricing and review quotes; skipped if absent |

## Database

The pipeline stores all data in `groupon.duckdb` (DuckDB). Query it directly:

```bash
python cli.py --query "SELECT * FROM audits WHERE avg_rating < 4"
python cli.py --query "SELECT slug, value_verdict FROM research"
python cli.py --query "SELECT slug, title_rewrite FROM proposals"
```

## Adding More Deals

Add URLs to `deals.txt` (one per line, `#` for comments) and run with `--resume` to process only new ones:

```bash
echo "https://www.groupon.com/deals/some-new-deal" >> deals.txt
python cli.py --urls deals.txt --resume
```

## Logs

- `errors.log` — all errors (scrape failures, AI failures, parse errors)
- `usage.log` — Anthropic API token usage per call (model, input_tokens, output_tokens)
