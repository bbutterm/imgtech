"""Персистентная очередь PDF-сравнений для self-hosted dev.

Очередь намеренно использует только stdlib SQLite: API и worker могут
перезапускаться независимо, а незавершённые jobs после рестарта возвращаются
в pending. Файлы хранятся рядом с БД и удаляются после успешного/неуспешного
завершения.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

DEFAULT_JOB_DIR = "/var/lib/imgtech-dev/jobs"
JOB_DIR = Path(os.environ.get("IMGTECH_JOB_DIR", DEFAULT_JOB_DIR))
DB_PATH = JOB_DIR / "jobs.sqlite3"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'succeeded', 'failed')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    stage TEXT NOT NULL DEFAULT 'queued',
    done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    file1 TEXT NOT NULL,
    file2 TEXT NOT NULL,
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_pending_idx ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS jobs_uid_idx ON jobs(uid, created_at DESC);
"""


def _now() -> float:
    return time.time()


def init_db() -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.executescript(_SCHEMA)


def _connect() -> sqlite3.Connection:
    init_db()
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def new_job(uid: str, data1: bytes, data2: bytes) -> str:
    if not uid:
        raise ValueError("uid is required")
    init_db()
    job_id = secrets.token_urlsafe(24)
    job_dir = JOB_DIR / job_id
    job_dir.mkdir(mode=0o700)
    file1 = job_dir / "1.pdf"
    file2 = job_dir / "2.pdf"
    try:
        file1.write_bytes(data1)
        file2.write_bytes(data2)
        now = _now()
        with sqlite3.connect(DB_PATH, timeout=30) as db:
            db.execute(
                "INSERT INTO jobs "
                "(id, uid, state, created_at, updated_at, file1, file2) "
                "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
                (job_id, uid, now, now, str(file1), str(file2)),
            )
            db.commit()
    except Exception:
        file1.unlink(missing_ok=True)
        file2.unlink(missing_ok=True)
        job_dir.rmdir()
        raise
    return job_id


def get_job(job_id: str, uid: str | None = None) -> dict | None:
    with _connect() as db:
        if uid is None:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM jobs WHERE id = ? AND uid = ?", (job_id, uid)
            ).fetchone()
    return dict(row) if row else None


def public_job(row: dict) -> dict:
    result = json.loads(row["result"]) if row.get("result") else None
    return {
        "id": row["id"],
        "state": row["state"],
        "stage": row["stage"],
        "done": row["done"],
        "total": row["total"],
        "result": result,
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def claim_next() -> dict | None:
    """Атомарно переводит самую старую pending job в running."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM jobs WHERE state = 'pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            db.commit()
            return None
        now = _now()
        db.execute(
            "UPDATE jobs SET state='running', stage='opening', updated_at=? WHERE id=?",
            (now, row["id"]),
        )
        db.commit()
        result = dict(row)
        result.update(state="running", stage="opening", updated_at=now)
        return result


def update_progress(job_id: str, *, stage: str, done: int, total: int) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE jobs SET stage=?, done=?, total=?, updated_at=? WHERE id=?",
            (stage, max(0, done), max(0, total), _now(), job_id),
        )
        db.commit()


def finish_job(job_id: str, result: dict) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE jobs SET state='succeeded', stage='done', done=total, "
            "result=?, error=NULL, updated_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), _now(), job_id),
        )
        db.commit()
    cleanup_job_files(job_id)


def fail_job(job_id: str, error: str) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE jobs SET state='failed', stage='failed', error=?, updated_at=? WHERE id=?",
            (error[:2000], _now(), job_id),
        )
        db.commit()
    cleanup_job_files(job_id)


def cleanup_job_files(job_id: str) -> None:
    job_dir = JOB_DIR / job_id
    for name in ("1.pdf", "2.pdf"):
        (job_dir / name).unlink(missing_ok=True)
    try:
        job_dir.rmdir()
    except OSError:
        pass


def recover_stale_jobs(max_age: int = 900) -> int:
    """Возвращает jobs, брошенные worker при рестарте, обратно в pending."""
    cutoff = _now() - max_age
    with _connect() as db:
        cur = db.execute(
            "UPDATE jobs SET state='pending', stage='queued', updated_at=? "
            "WHERE state='running' AND updated_at < ?",
            (_now(), cutoff),
        )
        db.commit()
        return cur.rowcount
