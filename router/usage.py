"""Usage tracking: SQLite-based request count and token consumption per model."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CST = timezone(timedelta(hours=8))

DB_PATH = Path(__file__).parent / "data" / "usage.db"


def _now_iso() -> str:
    return datetime.now(CST).isoformat()


class UsageDB:
    """Thread-safe SQLite usage tracker."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path), timeout=10)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    @contextmanager
    def _conn(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    model TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    role TEXT DEFAULT 'unknown',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cached_tokens INTEGER DEFAULT 0,
                    request_id TEXT,
                    status_code INTEGER,
                    latency_ms INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_timestamp
                ON usage_logs(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_model
                ON usage_logs(model)
            """)
        # 兼容旧表：在独立事务中添加 role 列，避免异常污染主事务
        existing = {row[1] for row in self._get_conn().execute("PRAGMA table_info(usage_logs)").fetchall()}
        if "role" not in existing:
            with self._conn() as conn:
                conn.execute("ALTER TABLE usage_logs ADD COLUMN role TEXT DEFAULT 'unknown'")

    def record(
        self,
        model: str,
        channel: str,
        role: str = "unknown",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        request_id: str | None = None,
        status_code: int = 200,
        latency_ms: int | None = None,
    ):
        """Record a single request's usage data."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO usage_logs
                   (timestamp, model, channel, role, prompt_tokens, completion_tokens,
                    cached_tokens, request_id, status_code, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now_iso(),
                    model,
                    channel,
                    role,
                    prompt_tokens,
                    completion_tokens,
                    cached_tokens,
                    request_id,
                    status_code,
                    latency_ms,
                ),
            )

    def get_summary(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        group_by: str = "model",
        role_filter: Optional[str] = None,
    ) -> list[dict]:
        """Get aggregated usage summary.

        Args:
            since: ISO timestamp start (default: 7 days ago)
            until: ISO timestamp end (default: now)
            group_by: "model" or "channel"
        """
        if since is None:
            since = (datetime.now(CST) - timedelta(days=7)).isoformat()
        if until is None:
            until = datetime.now(CST).isoformat()

        with self._conn() as conn:
            # role_filter 处理：
            #   None 或 "request" → 排除 classifier
            #   "classifier" → 只看 classifier
            #   其他 → 精确匹配
            if role_filter is None or role_filter == "request":
                role_clause = "AND (role IS NULL OR role != 'classifier')"
                params: tuple = (since, until)
            elif role_filter == "classifier":
                role_clause = "AND role = 'classifier'"
                params = (since, until)
            else:
                role_clause = "AND role = ?"
                params = (since, until, role_filter)

            rows = conn.execute(
                f"""
                SELECT
                    {group_by} as grp,
                    channel as grp_channel,
                    COUNT(*) as request_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(cached_tokens) as total_cached_tokens,
                    AVG(latency_ms) as avg_latency_ms
                FROM usage_logs
                WHERE timestamp >= ? AND timestamp <= ?
                {role_clause}
                GROUP BY {group_by}, channel
                ORDER BY request_count DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_daily(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        role_filter: Optional[str] = None,
    ) -> list[dict]:
        """Get daily usage breakdown across all models."""
        if since is None:
            since = (datetime.now(CST) - timedelta(days=7)).isoformat()
        if until is None:
            until = datetime.now(CST).isoformat()

        # role_filter 处理：None 或 'request' → 排除 classifier；'classifier' → 只看 classifier
        if role_filter is None or role_filter == "request":
            role_clause = "AND (role IS NULL OR role != 'classifier')"
            params: tuple = (since, until)
        elif role_filter == "classifier":
            role_clause = "AND role = 'classifier'"
            params = (since, until)
        else:
            role_clause = "AND role = ?"
            params = (since, until, role_filter)

        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    DATE(timestamp) as date,
                    model,
                    channel,
                    COUNT(*) as request_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(cached_tokens) as total_cached_tokens
                FROM usage_logs
                WHERE timestamp >= ? AND timestamp <= ?
                {role_clause}
                GROUP BY DATE(timestamp), model
                ORDER BY date DESC, request_count DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_hourly(self, date: Optional[str] = None, role_filter: Optional[str] = None) -> list[dict]:
        """Get hourly usage breakdown for a given date (default: today)."""
        if date is None:
            date = datetime.now(CST).strftime("%Y-%m-%d")
        since = f"{date}T00:00:00"
        until = f"{date}T23:59:59"

        if role_filter is None or role_filter == "request":
            role_clause = "AND (role IS NULL OR role != 'classifier')"
            params: tuple = (since, until)
        elif role_filter == "classifier":
            role_clause = "AND role = 'classifier'"
            params = (since, until)
        else:
            role_clause = "AND role = ?"
            params = (since, until, role_filter)

        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    CAST(strftime('%H', datetime(timestamp, '+8 hours')) AS INTEGER) as hour,
                    model,
                    channel,
                    COUNT(*) as request_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens
                FROM usage_logs
                WHERE timestamp >= ? AND timestamp <= ?
                {role_clause}
                GROUP BY hour, model
                ORDER BY hour ASC, request_count DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_role_stats(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> list[dict]:
        """Get request count grouped by role."""
        if since is None:
            since = (datetime.now(CST) - timedelta(days=7)).isoformat()
        if until is None:
            until = datetime.now(CST).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT role, COUNT(*) as request_count,
                       SUM(prompt_tokens) as total_prompt_tokens,
                       SUM(completion_tokens) as total_completion_tokens
                FROM usage_logs
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY role ORDER BY request_count DESC
                """,
                (since, until),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_logs(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get recent raw usage log entries."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM usage_logs
                WHERE role != 'classifier'
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_total_stats(self) -> dict:
        """Get overall lifetime stats (excluding classifier requests)."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_requests,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(cached_tokens) as total_cached_tokens
                FROM usage_logs
                WHERE role != 'classifier'
                """
            ).fetchone()
            return dict(row) if row else {}


# Singleton — initialized once at import time
_db: UsageDB | None = None


def init_usage_db(db_path: str | Path = DB_PATH) -> UsageDB:
    global _db
    _db = UsageDB(db_path)
    return _db


def get_usage_db() -> UsageDB:
    global _db
    if _db is None:
        _db = UsageDB()
    return _db
