"""DB 연결: DATABASE_URL이 있으면 Supabase(Postgres), 없으면 로컬 SQLite(data/app.db).

- 테이블은 schema.sql을 그대로 실행해 만듭니다(SQLite에서는 자료형만 바꿈).
- SQL은 %s 자리 표시자로 쓰고, SQLite에서는 ?로 바꿉니다.
- 연결은 하나만 만들어 다시 씁니다. 여러 사용자의 요청이 이 연결을 같이 쓰므로 lock으로 한 번에 하나씩 처리합니다.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.sql"
SQLITE_PATH = ROOT / "data" / "app.db"


class Conflict(Exception):
    """DB 유일성·검사 제약에 걸림(예: 마지막 자리에 다른 학생이 먼저 들어감)."""


def schema_statements(kind: str) -> list[str]:
    text = "\n".join(line for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines()
                     if not line.strip().startswith("--"))
    statements = [s.strip() for s in text.split(";") if s.strip()]
    if kind == "postgres":
        return statements
    result = []
    for sql in statements:
        if "ROW LEVEL SECURITY" in sql:
            continue
        sql = sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        sql = sql.replace("TIMESTAMPTZ", "TEXT").replace("now()", "CURRENT_TIMESTAMP")
        result.append(sql)
    return result


class Tx:
    """한 번의 읽기 또는 트랜잭션 안에서 쓰는 질의 도구."""

    def __init__(self, conn, kind: str):
        self._conn = conn
        self._kind = kind

    def _sql(self, sql: str) -> str:
        if self._kind == "sqlite":
            sql = sql.replace("%s", "?").replace(" FOR UPDATE", "").replace("now()", "CURRENT_TIMESTAMP")
        return sql

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._conn.execute(self._sql(sql), params)
        if cur.description is None:
            return []
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: tuple = ()) -> None:
        self._conn.execute(self._sql(sql), params)

    def many(self, sql: str, seq: list[tuple]) -> None:
        if seq:
            self._conn.cursor().executemany(self._sql(sql), seq)


class Database:
    def __init__(self, url: str | None = None, sqlite_path: Path = SQLITE_PATH):
        self.kind = "postgres" if url else "sqlite"
        self._url = url
        self._sqlite_path = sqlite_path
        self._lock = threading.Lock()
        self._conn = None
        self.write(lambda tx: [tx.run(sql) for sql in schema_statements(self.kind)])

    def _connect(self):
        if self.kind == "sqlite":
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._sqlite_path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        import psycopg

        return psycopg.connect(
            self._url,
            autocommit=True,
            connect_timeout=10,
            # Supabase Transaction pooler(6543)는 준비된 문장을 지원하지 않습니다.
            prepare_threshold=None,
            # 앱이 잠들었다 깬 뒤 끊긴 연결을 몇 분이 아니라 8초 안에 알아차리게 합니다.
            tcp_user_timeout=8000,
            keepalives=1,
            keepalives_idle=30,
        )

    def _is_disconnect(self, error: Exception) -> bool:
        if self.kind != "postgres":
            return False
        import psycopg

        return isinstance(error, psycopg.OperationalError)

    def _is_conflict(self, error: Exception) -> bool:
        if isinstance(error, sqlite3.IntegrityError):
            return True
        if self.kind == "postgres":
            import psycopg

            return isinstance(error, psycopg.IntegrityError)
        return False

    def _with_conn(self, fn: Callable[[Tx], Any], transaction: bool) -> Any:
        with self._lock:
            # 연결이 끊겨 있으면(잠든 앱이 깨어난 경우) 한 번 새로 연결해 다시 합니다.
            # 끊긴 연결에서는 COMMIT 전에 실패하므로 같은 일을 두 번 기록하지 않습니다.
            for attempt in (1, 2):
                if self._conn is None:
                    self._conn = self._connect()
                tx = Tx(self._conn, self.kind)
                try:
                    if not transaction:
                        return fn(tx)
                    self._conn.execute("BEGIN IMMEDIATE" if self.kind == "sqlite" else "BEGIN")
                    try:
                        result = fn(tx)
                    except BaseException:
                        try:
                            self._conn.execute("ROLLBACK")
                        except Exception:
                            pass
                        raise
                    self._conn.execute("COMMIT")
                    return result
                except Exception as e:
                    if self._is_conflict(e):
                        raise Conflict(str(e)) from None
                    if attempt == 1 and self._is_disconnect(e):
                        try:
                            self._conn.close()
                        except Exception:
                            pass
                        self._conn = None
                        continue
                    raise

    def read(self, fn: Callable[[Tx], Any]) -> Any:
        return self._with_conn(fn, transaction=False)

    def write(self, fn: Callable[[Tx], Any]) -> Any:
        """fn 전체를 한 트랜잭션으로 실행합니다. 예외가 나면 모두 되돌립니다."""
        return self._with_conn(fn, transaction=True)

