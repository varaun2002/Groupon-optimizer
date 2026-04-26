from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb

from models.deal import DealAudit, ResearchData, OptimizationProposal

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "groupon.duckdb"


class Store:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)
        self._con = duckdb.connect(self.db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS audits (
                url TEXT PRIMARY KEY,
                slug TEXT,
                category TEXT,
                city TEXT,
                avg_rating DOUBLE,
                num_sold TEXT,
                scraped_at TEXT,
                json_data TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS research (
                url TEXT PRIMARY KEY,
                slug TEXT,
                yelp_rating DOUBLE,
                yelp_review_count INTEGER,
                google_rating DOUBLE,
                researched_at TEXT,
                json_data TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                url TEXT PRIMARY KEY,
                slug TEXT,
                generated_at TEXT,
                json_data TEXT
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                url TEXT PRIMARY KEY,
                slug TEXT,
                status TEXT,
                created_at TEXT
            )
        """)

    def save_audit(self, audit: DealAudit) -> None:
        data = audit.model_dump_json()
        avg_rating = audit.trust_signals.avg_rating
        num_sold = audit.trust_signals.num_sold
        self._con.execute("""
            INSERT INTO audits (url, slug, category, city, avg_rating, num_sold, scraped_at, json_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (url) DO UPDATE SET
                slug = excluded.slug,
                category = excluded.category,
                city = excluded.city,
                avg_rating = excluded.avg_rating,
                num_sold = excluded.num_sold,
                scraped_at = excluded.scraped_at,
                json_data = excluded.json_data
        """, [audit.url, audit.slug, audit.category, audit.city, avg_rating, num_sold, audit.scraped_at, data])
        logger.info("Saved audit for %s", audit.slug)

    def save_research(self, research: ResearchData) -> None:
        data = research.model_dump_json()
        self._con.execute("""
            INSERT INTO research (url, slug, yelp_rating, yelp_review_count, google_rating, researched_at, json_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (url) DO UPDATE SET
                slug = excluded.slug,
                yelp_rating = excluded.yelp_rating,
                yelp_review_count = excluded.yelp_review_count,
                google_rating = excluded.google_rating,
                researched_at = excluded.researched_at,
                json_data = excluded.json_data
        """, [research.url, research.slug, research.yelp_rating, research.yelp_review_count,
              research.google_rating, research.researched_at, data])
        logger.info("Saved research for %s", research.slug)

    def save_proposal(self, proposal: OptimizationProposal) -> None:
        data = proposal.model_dump_json()
        self._con.execute("""
            INSERT INTO proposals (url, slug, generated_at, json_data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (url) DO UPDATE SET
                slug = excluded.slug,
                generated_at = excluded.generated_at,
                json_data = excluded.json_data
        """, [proposal.url, proposal.slug, proposal.generated_at, data])
        logger.info("Saved proposal for %s", proposal.slug)

    def get_audit(self, url: str) -> DealAudit | None:
        rows = self._con.execute(
            "SELECT json_data FROM audits WHERE url = ?", [url]
        ).fetchall()
        if not rows:
            return None
        return DealAudit.model_validate_json(rows[0][0])

    def get_research(self, url: str) -> ResearchData | None:
        rows = self._con.execute(
            "SELECT json_data FROM research WHERE url = ?", [url]
        ).fetchall()
        if not rows:
            return None
        return ResearchData.model_validate_json(rows[0][0])

    def list_completed_urls(self) -> list[str]:
        rows = self._con.execute(
            "SELECT url FROM proposals"
        ).fetchall()
        return [r[0] for r in rows]

    def query(self, sql: str) -> list[dict[str, Any]]:
        result = self._con.execute(sql)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def close(self) -> None:
        self._con.close()
