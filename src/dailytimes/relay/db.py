"""A deliberately small table API with two backends.

- `SQLiteDB`: local and self-hosted installs, zero setup.
- `SupabaseDB`: Supabase (or any PostgREST) over HTTPS with the service key, so it
  works from serverless hosts (Vercel) that can't hold Postgres connections. The
  browser never talks to the database; Row Level Security stays on with no policies.

Only equality, range and ordering filters are supported. That's all the store needs,
and it keeps both backends easy to audit.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

Row = dict[str, Any]


class Database(ABC):
    @abstractmethod
    def insert(self, table: str, row: Row) -> None: ...

    @abstractmethod
    def select(self, table: str, *, eq: Row | None = None, gte: Row | None = None, lt: Row | None = None,
               order: str | None = None, desc: bool = False, limit: int | None = None) -> list[Row]: ...

    @abstractmethod
    def update(self, table: str, values: Row, *, eq: Row | None = None, lt: Row | None = None) -> int: ...

    @abstractmethod
    def delete(self, table: str, *, eq: Row | None = None, lt: Row | None = None) -> int: ...

    @abstractmethod
    def upsert(self, table: str, row: Row, key: tuple[str, ...]) -> None: ...

    def one(self, table: str, **eq: Any) -> Row | None:
        rows = self.select(table, eq=eq, limit=1)
        return rows[0] if rows else None


class DuplicateError(ValueError):
    """A unique constraint was violated."""


# ------------------------------------------------------------------ SQLite

class SQLiteDB(Database):
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()

    @staticmethod
    def _where(eq: Row | None, gte: Row | None = None, lt: Row | None = None) -> tuple[str, list]:
        parts, args = [], []
        for op, cond in (("=", eq), (">=", gte), ("<", lt)):
            for k, v in (cond or {}).items():
                if v is None and op == "=":
                    parts.append(f"{k} IS NULL")
                else:
                    parts.append(f"{k} {op} ?")
                    args.append(v)
        return (" WHERE " + " AND ".join(parts)) if parts else "", args

    def insert(self, table, row):
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        try:
            with self._lock:
                self._db.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(row.values()))
        except sqlite3.IntegrityError as e:
            raise DuplicateError(str(e)) from e

    def select(self, table, *, eq=None, gte=None, lt=None, order=None, desc=False, limit=None):
        where, args = self._where(eq, gte, lt)
        sql = f"SELECT * FROM {table}{where}"
        if order:
            sql += f" ORDER BY {order} {'DESC' if desc else 'ASC'}, rowid {'DESC' if desc else 'ASC'}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def update(self, table, values, *, eq=None, lt=None):
        where, args = self._where(eq, None, lt)
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._lock:
            return self._db.execute(f"UPDATE {table} SET {sets}{where}", list(values.values()) + args).rowcount

    def delete(self, table, *, eq=None, lt=None):
        where, args = self._where(eq, None, lt)
        with self._lock:
            return self._db.execute(f"DELETE FROM {table}{where}", args).rowcount

    def upsert(self, table, row, key):
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        sets = ", ".join(f"{k} = excluded.{k}" for k in row if k not in key)
        with self._lock:
            self._db.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({qs}) ON CONFLICT ({', '.join(key)}) DO UPDATE SET {sets}",
                list(row.values()))


# ------------------------------------------------------------------ Supabase / PostgREST

class SupabaseDB(Database):
    def __init__(self, url: str, service_key: str, http: httpx.Client | None = None, schema: str | None = None):
        self.base = url.rstrip("/") + "/rest/v1"
        # New-style keys (sb_secret_...) go in `apikey` only; Supabase rejects them as a Bearer
        # token. Legacy JWT keys (service_role) need both headers.
        self.headers = {"apikey": service_key}
        if not service_key.startswith("sb_"):
            self.headers["Authorization"] = f"Bearer {service_key}"
        if schema:
            self.headers["Accept-Profile"] = self.headers["Content-Profile"] = schema
        self.http = http or httpx.Client(timeout=15)

    @staticmethod
    def _filters(eq: Row | None, gte: Row | None = None, lt: Row | None = None) -> list[tuple[str, str]]:
        params = []
        for op, cond in (("eq", eq), ("gte", gte), ("lt", lt)):
            for k, v in (cond or {}).items():
                if v is None and op == "eq":
                    params.append((k, "is.null"))
                else:
                    params.append((k, f"{op}.{v}"))
        return params

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code == 409:
            raise DuplicateError(r.text)
        if r.status_code >= 400:
            raise RuntimeError(f"supabase {r.request.method} {r.request.url.path}: {r.status_code} {r.text}")
        return r

    def insert(self, table, row):
        self._check(self.http.post(f"{self.base}/{table}", json=row,
                                   headers={**self.headers, "Prefer": "return=minimal"}))

    def select(self, table, *, eq=None, gte=None, lt=None, order=None, desc=False, limit=None):
        params = [("select", "*")] + self._filters(eq, gte, lt)
        if order:
            params.append(("order", f"{order}.{'desc' if desc else 'asc'}"))
        if limit:
            params.append(("limit", str(int(limit))))
        return self._check(self.http.get(f"{self.base}/{table}", params=params, headers=self.headers)).json()

    def update(self, table, values, *, eq=None, lt=None):
        r = self._check(self.http.patch(f"{self.base}/{table}", params=self._filters(eq, None, lt), json=values,
                                        headers={**self.headers, "Prefer": "return=representation"}))
        return len(r.json())

    def delete(self, table, *, eq=None, lt=None):
        r = self._check(self.http.delete(f"{self.base}/{table}", params=self._filters(eq, None, lt),
                                         headers={**self.headers, "Prefer": "return=representation"}))
        return len(r.json())

    def upsert(self, table, row, key):
        self._check(self.http.post(
            f"{self.base}/{table}", params={"on_conflict": ",".join(key)}, json=row,
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}))


def database_from_env() -> Database:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return SupabaseDB(url, key, schema=os.environ.get("SUPABASE_SCHEMA") or None)
    if os.environ.get("VERCEL"):
        raise RuntimeError("On Vercel the filesystem is read-only: set SUPABASE_URL and SUPABASE_SERVICE_KEY "
                           "(and load supabase/schema.sql once). See DEPLOY.md.")
    return SQLiteDB(os.environ.get("DAILYTIMES_DB", "dailytimes.db"))
