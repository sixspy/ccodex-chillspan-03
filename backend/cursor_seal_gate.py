"""游标封批闸：游标页/封批页/批次页/温谱页/对账页五页同源的唯一判定源。

闸门只有两种合法轨道，且两边不得混用：

* 甲轨 —— 直接正式封批（``POST /api/seals``）：游标未收口则正式表零增并拒写；
* 乙轨 —— 草稿封批意图（``POST /api/seal-intents``）+ 游标收口后原子晋升
  （``POST /api/seal-intents/{id}/promote``）；游标未收口就晋升则回滚意图。

任何偏离都写入 ``cursor_seal_ledger``（游标封批台账），台账当天封口。
"""
from __future__ import annotations

import sqlite3

FORMAL = "已封批"
OPEN = "开放"
DRAFT = "草稿"

DEVIATION = "游标封批偏航"


def gate_for_batch(conn: sqlite3.Connection, batch_id: int) -> dict | None:
    """返回批次当前游标封批闸快照；五页共用这一份，禁止各自另算。"""
    batch = conn.execute(
        "SELECT id,batch_code,status,generation,sealed_at FROM temp_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not batch:
        return None
    open_cursors = conn.execute(
        "SELECT COUNT(*) c FROM excursion_cursors WHERE batch_id=? AND status=?",
        (batch_id, OPEN),
    ).fetchone()["c"]
    pending_intent = conn.execute(
        "SELECT COUNT(*) c FROM seal_intents WHERE batch_id=? AND status=?",
        (batch_id, DRAFT),
    ).fetchone()["c"]
    formal_seals = conn.execute(
        "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"]
    formal_sealed = batch["status"] == FORMAL
    consistent = bool(
        not (open_cursors and formal_sealed)
        and not (pending_intent and formal_sealed)
        and (formal_sealed == (formal_seals >= 1))
    )
    return {
        "batch_id": int(batch["id"]),
        "batch_code": batch["batch_code"],
        "open_cursors": int(open_cursors),
        "pending_intent": bool(pending_intent),
        "formal_sealed": bool(formal_sealed),
        "formal_seals": int(formal_seals),
        "generation": int(batch["generation"]),
        "blocked": bool(open_cursors or pending_intent),
        "track": "乙" if pending_intent else "甲",
        "consistent": consistent,
    }


def attach_gate(conn: sqlite3.Connection, rows, id_key: str = "batch_id") -> list[dict]:
    """给列表行挂同一份 gate 快照，保证多页账实同源。"""
    cache: dict[int, dict | None] = {}
    out = []
    for row in rows:
        d = dict(row)
        bid = int(d[id_key])
        if bid not in cache:
            cache[bid] = gate_for_batch(conn, bid)
        d["gate"] = cache[bid]
        out.append(d)
    return out


def write_ledger(
    conn: sqlite3.Connection,
    *,
    now: str,
    batch_id: int,
    actor: str,
    track: str,
    decision: str,
    deviation: str = "",
    detail: str = "",
    cursor_id: int | None = None,
    intent_id: int | None = None,
) -> int:
    """写入游标封批台账；``closed_at`` 与 ``created_at`` 同日，当天封口。"""
    cur = conn.execute(
        """INSERT INTO cursor_seal_ledger(batch_id,cursor_id,intent_id,track,decision,
           deviation,detail,actor,created_at,closed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            batch_id,
            cursor_id,
            intent_id,
            track,
            decision,
            deviation,
            detail,
            actor,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def open_cursor_id(conn: sqlite3.Connection, batch_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM excursion_cursors WHERE batch_id=? AND status=? ORDER BY id LIMIT 1",
        (batch_id, OPEN),
    ).fetchone()
    return int(row["id"]) if row else None
