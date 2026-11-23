"""仓间锁与争用登记（公共并发面）。"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def cursor_seal_lock_key(batch_id: int) -> str:
    """游标 ≡ 封批 共用的一把锁：开放游标与封批不得交叉执行。"""
    return f"cursor-seal:batch:{batch_id}"


def record_contention(conn, lock_key: str, actor: str, detail: str) -> None:
    """业务层后到争用痕：立即提交，避免被外层的回滚吃掉。"""
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, actor, "争用", detail, now_iso()),
    )
    conn.commit()


def _log_contention(conn, lock_key: str, holder: str, held_by: str, actor: str) -> None:
    """后到者的争用痕必须落盘：拒绝响应不能让争用记录被回滚。"""
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, actor, "争用", f"持有者={held_by}", now_iso()),
    )
    conn.commit()


def try_acquire(conn, lock_key: str, holder: str, actor: str | None = None) -> bool:
    now = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO chamber_locks(lock_key,holder,acquired_at)"
        " VALUES (?,?,?)",
        (lock_key, holder, now),
    )
    row = conn.execute(
        "SELECT holder FROM chamber_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is None or row["holder"] == holder:
        return True
    _log_contention(conn, lock_key, holder, row["holder"], actor or holder)
    return False


@contextmanager
def cursor_seal_guard(conn, batch_id: int, holder: str):
    """抢游标封批锁并持有到业务落盘。

    持锁者按“请求”而非“账号”区分：同一账号并发发起游标与封批也必须互斥，
    否则同 holder 会被误判为可重入而交叉写。
    ``BEGIN IMMEDIATE`` 串行化同库写事务；锁行立刻提交，后到者读得到持有者，
    只写争用痕并 409，业务一律不写。退出时先回滚未提交改动，再释放锁并提交。
    """
    lock_key = cursor_seal_lock_key(batch_id)
    actor = holder
    holder = f"{holder}#{uuid.uuid4().hex[:8]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        pass  # 已在写事务中，由既有的 IMMEDIATE 事务承担串行化
    if not try_acquire(conn, lock_key, holder, actor):
        conn.rollback()
        raise HTTPException(409, "争用：游标封批锁被占用，后到只记争用不落封批")
    conn.commit()
    try:
        yield lock_key
    finally:
        try:
            conn.rollback()
        except sqlite3.OperationalError:
            pass
        release(conn, lock_key, holder)
        conn.commit()


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM chamber_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chamber_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}