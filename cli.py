#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

load_dotenv(override=True)

# Configure logging BEFORE any module imports so all loggers inherit settings
log_dir = Path(__file__).parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "errors.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
usage_handler = logging.FileHandler(log_dir / "usage.log")
usage_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.getLogger("usage").addHandler(usage_handler)
logging.getLogger("usage").setLevel(logging.INFO)
logging.getLogger("usage").propagate = False

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("cli")
console = Console()


def _load_urls_from_file(path: str) -> list[str]:
    urls = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            url = line.split("#")[0].strip()
            if url.startswith("http"):
                urls.append(url)
    return urls


def _write_outputs(
    audit,
    research,
    proposal,
    analysis: dict,
    output_dir: Path,
) -> None:
    from models.deal import DealAudit, ResearchData, OptimizationProposal
    slug = audit.slug
    deal_dir = output_dir / slug
    deal_dir.mkdir(parents=True, exist_ok=True)

    # audit.json
    with open(deal_dir / "audit.json", "w") as f:
        f.write(audit.model_dump_json(indent=2))

    # audit_summary.md
    pricing_rows = ""
    for opt in audit.pricing_options:
        orig = f"${opt.original_price:.2f}" if opt.original_price else "N/A"
        deal = f"${opt.deal_price:.2f}" if opt.deal_price else "N/A"
        disc = f"{opt.discount_pct:.0f}%" if opt.discount_pct else "N/A"
        sav = f"${opt.savings:.2f}" if opt.savings else "N/A"
        pricing_rows += f"| {opt.name} | {orig} | {deal} | {disc} | {sav} |\n"

    highlights_md = "\n".join(f"- {h}" for h in audit.highlights) or "- No highlights extracted"
    fine_print_md = "\n".join(f"- {fp}" for fp in audit.fine_print) or "- No fine print extracted"

    faqs_md = ""
    if audit.faqs:
        for faq in audit.faqs:
            faqs_md += f"**Q: {faq.question}**\nA: {faq.answer}\n\n"
    else:
        faqs_md = "No FAQs found on this page."

    weaknesses_md = ""
    strengths_md = ""
    scores_rows = ""
    img_quality = "N/A"
    faq_assessment = "N/A"

    if analysis and "error" not in analysis:
        tc = analysis.get("title_clarity_score", "N/A")
        tc_r = analysis.get("title_clarity_reasoning", "")
        ts = analysis.get("trust_signal_strength", "N/A")
        ts_r = analysis.get("trust_signal_reasoning", "")
        ue = analysis.get("urgency_effectiveness", "N/A")
        ue_r = analysis.get("urgency_reasoning", "")
        scores_rows = (
            f"| Title clarity | {tc}/10 | {tc_r} |\n"
            f"| Trust signals | {ts}/10 | {ts_r} |\n"
            f"| Urgency | {ue}/10 | {ue_r} |\n"
        )
        img_quality = analysis.get("image_quality_assessment", "N/A")
        faq_assessment = analysis.get("faq_assessment", "N/A")
        for w in analysis.get("top_3_weaknesses", []):
            weaknesses_md += f"- **{w.get('finding', '')}** — {w.get('evidence', '')}\n"
        for s in analysis.get("top_3_strengths", []):
            strengths_md += f"- **{s.get('finding', '')}** — {s.get('evidence', '')}\n"

    audit_md = f"""# {audit.title}

**Merchant:** {audit.merchant_name} | **City:** {audit.city} | **Category:** {audit.category}
**Rating:** {audit.trust_signals.avg_rating or "N/A"} | **Sold:** {audit.trust_signals.num_sold or "N/A"} | **Scraped:** {audit.scraped_at}

---

## Pricing Options

| Option | Original | Deal Price | Discount | Savings |
|--------|----------|------------|----------|---------|
{pricing_rows or "| No pricing extracted | — | — | — | — |\n"}

## What You Get

{highlights_md}

## Fine Print

{fine_print_md}

## FAQs

{faqs_md}

## Page Technical Signals

- **Images:** {audit.images.count} | **Scripts:** {audit.script_count} | **Stylesheets:** {audit.stylesheet_count}
- **Mobile viewport:** {audit.mobile_viewport_meta or "Not found"}
- **Schema types:** {", ".join(audit.seo.schema_types) or "None detected"}

## AI Audit Scores

| Signal | Score | Reasoning |
|--------|-------|-----------|
{scores_rows or "| N/A | N/A | AI analysis not available |\n"}

## Image Quality Assessment

{img_quality}

## FAQ Assessment

{faq_assessment}

## Top Weaknesses

{weaknesses_md or "- No weaknesses identified\n"}

## Top Strengths

{strengths_md or "- No strengths identified\n"}
"""
    with open(deal_dir / "audit_summary.md", "w") as f:
        f.write(audit_md)

    # research.md
    competitor_rows = ""
    for c in research.competitor_prices:
        competitor_rows += f"| {c.get('name','N/A')} | {c.get('price','N/A')} | {c.get('source_url','N/A')} |\n"

    quotes_md = ""
    for q in research.review_quotes:
        quotes_md += f'> "{q.get("text","")}" — {q.get("rating","?")}★ via {q.get("source","")}\n\n'

    sources_md = "\n".join(f"- {s}" for s in research.sources) or "- No sources collected"
    gaps_md = "\n".join(f"- {g}" for g in research.content_gaps) or "- No gaps identified"

    research_md = f"""# Competitive Research: {audit.title}

## Competitor Pricing

| Business | Price | Source |
|----------|-------|--------|
{competitor_rows or "| No competitor data found | — | — |\n"}

## Merchant Direct Price

{research.merchant_direct_price or "Not found"}

## Merchant Reputation

**Yelp:** {research.yelp_rating or "N/A"}/5 ({research.yelp_review_count or "N/A"} reviews) | **Google:** {research.google_rating or "N/A"}/5

## Review Quotes

{quotes_md or "_No review quotes collected._\n"}

## Deal Value Assessment

{research.value_verdict or "Assessment not available."}

## Category Benchmark

Typical discount range for **{audit.category}**: {research.category_typical_discount or "Not determined"}

## Content Gaps

{gaps_md}

## Sources

{sources_md}
"""
    with open(deal_dir / "research.md", "w") as f:
        f.write(research_md)

    # proposal.md
    priority_md = ""
    for item in proposal.priority_ranking:
        rank = item.get("rank", "?")
        change = item.get("change", "")
        reasoning = item.get("reasoning", "")
        citation = item.get("data_citation", "")
        impact = item.get("expected_impact", "")
        priority_md += f"""### {rank}. {change} *(Expected impact: {impact})*

**What:** {change}
**Why:** {reasoning}
**Data:** {citation}

"""

    missing_md = "\n".join(f"- {m}" for m in proposal.missing_content) or "- None identified"
    img_rec_md = "\n".join(f"- {r}" for r in proposal.image_recommendations) or "- None identified"
    highlights_rw_md = "\n".join(f"- {h}" for h in proposal.highlights_rewrite) or "- None"

    seo = proposal.seo_improvements
    proposal_md = f"""# Optimization Proposal: {audit.title}

## Priority Recommendations

{priority_md or "_No priority recommendations generated._\n"}

---

## Title Rewrite

**Current:** {audit.title}
**Proposed:** {proposal.title_rewrite}
**Reasoning:** {proposal.title_reasoning}

---

## SEO Improvements

| Field | Proposed Value |
|-------|----------------|
| Meta title | {seo.get("meta_title", "N/A")} |
| Meta description | {seo.get("meta_description", "N/A")} |
| H1 | {seo.get("h1", "N/A")} |
| Schema | {seo.get("schema", "N/A")} |

---

## Rewritten Highlights

{highlights_rw_md}

---

## Missing Content to Add

{missing_md}

---

## Image Recommendations

{img_rec_md}

---

## Competitive Positioning

{proposal.competitive_positioning or "Not available."}
"""
    with open(deal_dir / "proposal.md", "w") as f:
        f.write(proposal_md)

    logger.info("Wrote output files for %s -> %s", slug, deal_dir)


def _process_deal(url: str, output_dir: Path, store) -> bool:
    from pipeline.scraper import scrape
    from pipeline.researcher import research
    from pipeline.ai.client import analyze_audit, synthesize_research, generate_proposal

    slug = url.rstrip("/").split("/")[-1]
    steps = {}

    try:
        console.print(f"  [cyan]Scraping[/cyan] {slug}...")
        audit = scrape(url)
        store.save_audit(audit)
        steps["scrape"] = "✓"
    except Exception as e:
        logger.error("Scrape failed %s: %s", url, e, exc_info=True)
        steps["scrape"] = "✗"
        from models.deal import DealAudit
        from datetime import timezone
        audit = DealAudit(
            url=url, slug=slug,
            title=f"[Failed] {slug}",
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        console.print(f"  [cyan]Researching[/cyan] {slug}...")
        partial_research, raw_data = research(audit)
        steps["research"] = "✓"
    except Exception as e:
        logger.error("Research failed %s: %s", url, e, exc_info=True)
        steps["research"] = "✗"
        from models.deal import ResearchData
        from datetime import timezone
        partial_research = ResearchData(
            url=url, slug=slug,
            researched_at=datetime.now(timezone.utc).isoformat(),
        )
        raw_data = {}

    try:
        console.print(f"  [cyan]AI audit analysis[/cyan] {slug}...")
        analysis = analyze_audit(audit)
        steps["ai_audit"] = "✓"
    except Exception as e:
        logger.error("AI audit analysis failed %s: %s", url, e, exc_info=True)
        steps["ai_audit"] = "✗"
        analysis = {"error": str(e)}

    try:
        console.print(f"  [cyan]AI research synthesis[/cyan] {slug}...")
        full_research = synthesize_research(raw_data, audit)
        store.save_research(full_research)
        steps["ai_research"] = "✓"
    except Exception as e:
        logger.error("Research synthesis failed %s: %s", url, e, exc_info=True)
        steps["ai_research"] = "✗"
        full_research = partial_research
        store.save_research(full_research)

    try:
        console.print(f"  [cyan]AI proposal[/cyan] {slug}...")
        proposal = generate_proposal(audit, full_research, analysis)
        store.save_proposal(proposal)
        steps["ai_proposal"] = "✓"
    except Exception as e:
        logger.error("Proposal failed %s: %s", url, e, exc_info=True)
        steps["ai_proposal"] = "✗"
        from models.deal import OptimizationProposal
        from datetime import timezone
        proposal = OptimizationProposal(
            url=url, slug=slug,
            title_rewrite=f"[Error] {e}",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        _write_outputs(audit, full_research, proposal, analysis, output_dir)
        steps["output"] = "✓"
    except Exception as e:
        logger.error("Output write failed %s: %s", url, e, exc_info=True)
        steps["output"] = "✗"

    status_str = " ".join(f"{k}:{v}" for k, v in steps.items())
    all_ok = all(v == "✓" for v in steps.values())
    color = "green" if all_ok else "yellow"
    console.print(f"  [{color}]{status_str}[/{color}]")
    return all_ok


@click.command()
@click.option("--urls", "urls_file", default=None, help="Path to file with Groupon URLs (one per line)")
@click.option("--url", "single_url", default=None, help="Process a single Groupon URL")
@click.option("--resume", is_flag=True, default=False, help="Skip URLs already completed")
@click.option("--output-dir", default="./output", show_default=True, help="Output directory")
@click.option("--query", default=None, help="Run raw SQL against the DuckDB database and print results")
def main(urls_file, single_url, resume, output_dir, query):
    """Groupon deal optimizer pipeline."""
    from pipeline.store import Store

    store = Store()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if query:
        results = store.query(query)
        if not results:
            console.print("[yellow]No results.[/yellow]")
        else:
            table = Table(show_header=True, header_style="bold cyan")
            for col in results[0].keys():
                table.add_column(col)
            for row in results:
                table.add_row(*[str(v) if v is not None else "" for v in row.values()])
            console.print(table)
        store.close()
        return

    # Collect URLs
    urls: list[str] = []
    if single_url:
        urls = [single_url]
    elif urls_file:
        urls = _load_urls_from_file(urls_file)
    else:
        console.print("[red]Provide --url or --urls[/red]")
        store.close()
        sys.exit(1)

    if resume:
        completed = set(store.list_completed_urls())
        before = len(urls)
        urls = [u for u in urls if u not in completed]
        console.print(f"[yellow]Resume: skipping {before - len(urls)} already-completed deals[/yellow]")

    console.print(f"[bold]Processing {len(urls)} deals → {out}[/bold]\n")

    start_time = time.time()
    succeeded = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        console.print(f"\n[bold blue][{i}/{len(urls)}][/bold blue] {slug}")
        try:
            ok = _process_deal(url, out, store)
            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("Unhandled error for %s: %s", url, e, exc_info=True)
            failed += 1

        if i < len(urls):
            delay = 2.5
            console.print(f"  [dim]Waiting {delay}s before next deal...[/dim]")
            time.sleep(delay)

    elapsed = time.time() - start_time

    console.print("\n")
    summary = Table(title="Pipeline Summary", show_header=True, header_style="bold")
    summary.add_column("Metric")
    summary.add_column("Value")
    summary.add_row("Deals processed", str(succeeded + failed))
    summary.add_row("Succeeded", f"[green]{succeeded}[/green]")
    summary.add_row("Failed", f"[red]{failed}[/red]")
    summary.add_row("Total time", f"{elapsed:.1f}s")
    summary.add_row("Output dir", str(out.resolve()))
    console.print(summary)

    store.close()


if __name__ == "__main__":
    main()
