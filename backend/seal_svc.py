"""封批升代 + 快照清单 + 游标封批双轨闸。

甲轨：``POST /api/seals`` 直接正式封批——开放游标未收口则正式表零增、拒写并记偏航台账。
乙轨：``POST /api/seal-intents`` 先落草稿意图（批次正式零改），游标收口后
      ``POST /api/seal-intents/{id}/promote`` 原子晋升；未收口就晋升则回滚意图。

两轨共用 ``cursor-seal:batch:<id>`` 一把锁，互斥且不得混用；后到只记争用痕。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from cursor_seal_gate import (
    DEVIATION,
    DRAFT,
    attach_gate,
    gate_for_batch,
    open_cursor_id,
    write_ledger,
)
from db import connect, now_iso, write_audit
from locks import cursor_seal_guard, cursor_seal_lock_key, record_contention
from models import SealIn, SealIntentIn
from validators import require_calibration_fresh, validate_batch_sealable

router = APIRouter(tags=["seal"])


def _load_batch(conn, batch_id: int):
    row = conn.execute(
        """SELECT b.*, p.calibration_due, p.probe_code
           FROM temp_batches b JOIN probes p ON p.id=b.probe_id
           WHERE b.id=?""",
        (batch_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "批次不存在")
    return row


def _build_manifest(batch, manifest: str) -> str:
    if manifest:
        return manifest
    return json.dumps(
        {
            "batch_code": batch["batch_code"],
            "generation": int(batch["generation"]) + 1,
            "point_count": len(json.loads(batch["temp_points"] or "[]")),
            "min": batch["min_observed"],
            "max": batch["max_observed"],
            "open_cursors": 0,
        },
        ensure_ascii=False,
    )


def _formal_seal(conn, batch, manifest: str) -> dict:
    gen = int(batch["generation"])
    now = now_iso()
    cur = conn.execute(
        """INSERT INTO seal_snapshots(batch_id,batch_generation,manifest,status,sealed_at)
           VALUES (?,?,?,?,?)""",
        (batch["id"], gen + 1, _build_manifest(batch, manifest), "已封批", now),
    )
    conn.execute(
        """UPDATE temp_batches SET status='已封批', generation=generation+1,
           sealed_at=? WHERE id=?""",
        (now, batch["id"]),
    )
    return {"seal_id": int(cur.lastrowid), "generation": gen + 1, "sealed_at": now}


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, b.batch_code FROM seal_snapshots s
               JOIN temp_batches b ON b.id=s.batch_id ORDER BY s.id DESC"""
        ).fetchall()
        return {"items": attach_gate(conn, rows)}


@router.post("/api/seals")
def seal_batch(body: SealIn, authorization: AuthHeader = None):
    """甲轨：直接正式封批。"""
    user = require_writer(authorization)
    with connect() as conn:
        with cursor_seal_guard(conn, body.batch_id, user):
            batch = _load_batch(conn, body.batch_id)
            open_now = conn.execute(
                """SELECT COUNT(*) c FROM excursion_cursors
                   WHERE batch_id=? AND status='开放'""",
                (body.batch_id,),
            ).fetchone()["c"]
            if open_now:
                now = now_iso()
                write_ledger(
                    conn,
                    now=now,
                    batch_id=body.batch_id,
                    actor=user,
                    track="甲",
                    decision="拒写",
                    deviation=DEVIATION,
                    detail="开放游标未收口，正式封批表零增",
                    cursor_id=open_cursor_id(conn, body.batch_id),
                )
                conn.commit()
                raise HTTPException(
                    409, "存在开放超温游标，须先闭环；已记游标封批偏航台账"
                )
            draft = conn.execute(
                "SELECT id FROM seal_intents WHERE batch_id=? AND status=?",
                (body.batch_id, DRAFT),
            ).fetchone()
            if draft:
                now = now_iso()
                write_ledger(
                    conn,
                    now=now,
                    batch_id=body.batch_id,
                    actor=user,
                    track="甲",
                    decision="拒写",
                    deviation=DEVIATION,
                    detail=f"已有草稿封批意图#{int(draft['id'])}，两轨不得混用",
                    intent_id=int(draft["id"]),
                )
                record_contention(
                    conn,
                    cursor_seal_lock_key(body.batch_id),
                    user,
                    f"甲轨直封撞乙轨草稿意图#{int(draft['id'])}",
                )
                raise HTTPException(
                    409, "已有草稿封批意图，两轨不得混用；请走乙轨晋升"
                )
            bdict = dict(batch)
            bdict["temp_points"] = batch["temp_points"]
            validate_batch_sealable(bdict, open_now)
            require_calibration_fresh(batch)
            sealed = _formal_seal(conn, batch, body.manifest)
            write_audit(
                conn,
                user,
                "batch_seal",
                batch["batch_code"],
                {"seal_id": sealed["seal_id"], "generation": sealed["generation"]},
            )
            conn.commit()
            return {
                "seal_id": sealed["seal_id"],
                "batch_code": batch["batch_code"],
                "generation": sealed["generation"],
                "status": "已封批",
            }


@router.get("/api/seal-intents")
def list_seal_intents(
    authorization: AuthHeader = None,
    batch_id: int | None = None,
    status: str | None = None,
):
    require_user(authorization)
    with connect() as conn:
        q = "SELECT * FROM seal_intents"
        clauses, params = [], []
        if batch_id is not None:
            clauses.append("batch_id=?")
            params.append(batch_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(q + " ORDER BY id DESC", params).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/seal-intents", status_code=202)
def create_seal_intent(body: SealIntentIn, authorization: AuthHeader = None):
    """乙轨第一步：只落草稿意图，批次正式零改、正式封批表零增。"""
    user = require_writer(authorization)
    with connect() as conn:
        with cursor_seal_guard(conn, body.batch_id, user):
            batch = _load_batch(conn, body.batch_id)
            if batch["status"] == "已封批":
                raise HTTPException(409, "批次已封批")
            draft = conn.execute(
                "SELECT id FROM seal_intents WHERE batch_id=? AND status=?",
                (body.batch_id, DRAFT),
            ).fetchone()
            if draft:
                record_contention(
                    conn,
                    cursor_seal_lock_key(body.batch_id),
                    user,
                    f"该批次已有草稿封批意图#{int(draft['id'])}",
                )
                raise HTTPException(409, "争用：该批次已有草稿封批意图")
            manifest = _build_manifest(batch, body.manifest)
            cur = conn.execute(
                """INSERT INTO seal_intents(batch_id,status,manifest,actor,created_at)
                   VALUES (?,?,?,?,?)""",
                (body.batch_id, DRAFT, manifest, user, now_iso()),
            )
            write_audit(
                conn,
                user,
                "seal_intent_draft",
                batch["batch_code"],
                {"intent_id": int(cur.lastrowid)},
            )
            conn.commit()
            return {
                "id": int(cur.lastrowid),
                "batch_id": body.batch_id,
                "status": DRAFT,
            }


@router.post("/api/seal-intents/{intent_id}/promote")
def promote_seal_intent(intent_id: int, authorization: AuthHeader = None):
    """乙轨第二步：游标已收口才原子晋升；否则回滚意图，正式表零增。"""
    user = require_writer(authorization)
    with connect() as conn:
        intent = conn.execute(
            "SELECT * FROM seal_intents WHERE id=?", (intent_id,)
        ).fetchone()
        if not intent:
            raise HTTPException(404, "封批意图不存在")
        with cursor_seal_guard(conn, intent["batch_id"], user):
            intent = conn.execute(
                "SELECT * FROM seal_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if intent["status"] != DRAFT:
                raise HTTPException(409, f"封批意图状态为 {intent['status']}，不可晋升")
            batch = _load_batch(conn, intent["batch_id"])
            now = now_iso()
            open_now = conn.execute(
                """SELECT COUNT(*) c FROM excursion_cursors
                   WHERE batch_id=? AND status='开放'""",
                (batch["id"],),
            ).fetchone()["c"]
            if open_now:
                cursor_id = open_cursor_id(conn, batch["id"])
                conn.execute(
                    "UPDATE seal_intents SET status='已回滚', settled_at=? WHERE id=?",
                    (now, intent_id),
                )
                write_ledger(
                    conn,
                    now=now,
                    batch_id=batch["id"],
                    actor=user,
                    track="乙",
                    decision="回滚",
                    deviation=DEVIATION,
                    detail="开放游标未收口，草稿封批意图回滚，正式表零增",
                    cursor_id=cursor_id,
                    intent_id=intent_id,
                )
                write_audit(
                    conn,
                    user,
                    "seal_intent_rollback",
                    batch["batch_code"],
                    {"intent_id": intent_id},
                )
                conn.commit()
                raise HTTPException(
                    409, "存在开放超温游标，草稿封批意图已回滚；正式表零增"
                )
            bdict = dict(batch)
            bdict["temp_points"] = batch["temp_points"]
            validate_batch_sealable(bdict, open_now)
            require_calibration_fresh(batch)
            sealed = _formal_seal(conn, batch, intent["manifest"])
            conn.execute(
                """UPDATE seal_intents SET status='已晋升', settled_at=?, seal_id=?
                   WHERE id=?""",
                (now, sealed["seal_id"], intent_id),
            )
            write_ledger(
                conn,
                now=now,
                batch_id=batch["id"],
                actor=user,
                track="乙",
                decision="晋升",
                detail="游标已收口，草稿意图原子晋升正式封批",
                intent_id=intent_id,
            )
            write_audit(
                conn,
                user,
                "seal_intent_promote",
                batch["batch_code"],
                {"intent_id": intent_id, "seal_id": sealed["seal_id"]},
            )
            conn.commit()
            return {
                "intent_id": intent_id,
                "seal_id": sealed["seal_id"],
                "batch_code": batch["batch_code"],
                "generation": sealed["generation"],
                "status": "已封批",
            }


@router.get("/api/cursor-seal-ledger")
def list_cursor_seal_ledger(
    authorization: AuthHeader = None, batch_id: int | None = None
):
    """游标封批台账只读（当天封口）。"""
    require_user(authorization)
    with connect() as conn:
        if batch_id is None:
            rows = conn.execute(
                "SELECT * FROM cursor_seal_ledger ORDER BY id DESC LIMIT 200"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cursor_seal_ledger WHERE batch_id=? ORDER BY id DESC",
                (batch_id,),
            ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/cursor-seal-gate/{batch_id}")
def get_gate(batch_id: int, authorization: AuthHeader = None):
    """单批次闸快照：五页与后端共用同一判定源。"""
    require_user(authorization)
    with connect() as conn:
        gate = gate_for_batch(conn, batch_id)
        if gate is None:
            raise HTTPException(404, "批次不存在")
        return gate
