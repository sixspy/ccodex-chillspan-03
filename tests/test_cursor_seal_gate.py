"""游标封批闸三份失败测（甲 / 乙 / 争用）。

背景：旧用例 ``test_seal_blocks_open_excursion`` 名字是“封批拦截开放游标”，
实际只验证了反向——“已封批禁止再开游标”，从没在“游标仍开放”时真正点过封批。
那是假绿夹具。本模块先造“开放游标仍可封批”的夹具，再用三份可执行失败测盯住：

* 失败测甲：游标开着，封批却已落正式表（seal_snapshots 非零 / 批次已封批）；
* 失败测乙：草稿封批意图未晋升，却被任何一页算成“已封批”；
* 争用失败测：双人同时给同一开放游标批次抢封批，后到者只该留争用痕，不落封批。

三测必须先在旧码上见红，才允许改游标 / 封批 / 批次 / 温谱服务与两页。
"""
from __future__ import annotations

import datetime
import threading

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


# ---------------------------------------------------------------------------
# 假绿夹具：造一个“开放游标仍可封批”的批次现场（本地文件库，可跑）
# ---------------------------------------------------------------------------
def _open_cursor_batch(c: TestClient, h: dict, code: str) -> tuple[dict, dict]:
    """建批次 → 开游标，返回 (batch, cursor)。探头选用校准未到期者。"""
    chambers = c.get("/api/chambers", headers=h).json()["items"]
    ch = next(x for x in chambers if x["chamber_code"] == "CK-HN-03")
    probe = next(
        p
        for p in c.get(f"/api/probes?chamber_id={ch['id']}", headers=h).json()["items"]
        if p["probe_code"] == "PR-HN-C1"
    )
    batch = c.post(
        "/api/batches",
        headers=h,
        json={
            "chamber_id": ch["id"],
            "probe_id": probe["id"],
            "cargo_ref": "游标封批闸验收货载",
            "batch_code": code,
            "temp_points": [
                {"t": "10:00", "c": 4.0},
                {"t": "11:00", "c": 9.5},
                {"t": "12:00", "c": 5.0},
            ],
        },
    ).json()
    assert batch["status"] == "已入库"
    exc = c.post(
        "/api/excursions/open",
        headers=h,
        json={"batch_id": batch["id"], "threshold": 7.0, "note": "验收：门开升温"},
    )
    assert exc.status_code == 200, exc.text
    assert exc.json()["status"] == "开放"
    return batch, exc.json()


def _ledger(c: TestClient, h: dict, batch_id: int) -> list[dict]:
    r = c.get(
        "/api/cursor-seal-ledger",
        headers=h,
        params={"batch_id": batch_id},
    )
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _assert_no_formal_trace(batch_id: int) -> None:
    """正式封批表零增、批次正式字段零改：甲轨的核心断言。"""
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]
        assert n == 0, "开放游标下正式封批表竟有快照"
        crossed = conn.execute(
            """SELECT s.id FROM seal_snapshots s
               JOIN excursion_cursors e ON e.batch_id=s.batch_id AND e.status='开放'
               WHERE s.batch_id=?""",
            (batch_id,),
        ).fetchall()
        assert crossed == [], "快照带着未收口异常段"
        batch = conn.execute(
            "SELECT status,generation,sealed_at FROM temp_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        assert batch["status"] != "已封批", "游标仍开放却已正式封批"
        assert batch["generation"] == 1
        assert batch["sealed_at"] is None


def _gate_from_excursions(c, h, batch_id) -> dict:
    rows = c.get("/api/excursions", headers=h).json()["items"]
    row = next(x for x in rows if x["batch_id"] == batch_id)
    assert "gate" in row, "游标页未与封批闸同源"
    return row["gate"]


def _gate_from_seals(c, h, batch_id) -> dict:
    rows = c.get("/api/seals", headers=h).json()["items"]
    row = next(x for x in rows if x["batch_id"] == batch_id)
    assert "gate" in row, "封批页未与封批闸同源"
    return row["gate"]


def _gate_from_batch(c, h, batch_id) -> dict:
    detail = c.get(f"/api/batches/{batch_id}", headers=h).json()
    assert "gate" in detail, "批次页未与封批闸同源"
    return detail["gate"]


def _gate_from_spectrum(c, h, batch_id) -> dict:
    rows = c.get("/api/spectrum", headers=h).json()["items"]
    row = next(x for x in rows if x["batch_id"] == batch_id)
    assert "gate" in row, "温谱页未与封批闸同源"
    return row["gate"]


def _gate_from_proof(c, h, batch_code) -> dict:
    proof = c.get(f"/api/proof/batch/{batch_code}", headers=h).json()
    assert "gate" in proof, "对账页未与封批闸同源"
    return proof["gate"]


# ---------------------------------------------------------------------------
# 失败测甲：游标开着却已正式封批
# ---------------------------------------------------------------------------
def test_fail_a_open_cursor_must_refuse_formal_seal():
    c = TestClient(app)
    h = _auth(c)
    batch, cursor = _open_cursor_batch(c, h, "TB-GATE-A-001")

    r = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]})

    # 甲路径：拒写，正式表零增，批次正式零改
    assert r.status_code == 409, r.text
    assert "游标" in r.json()["detail"]
    _assert_no_formal_trace(batch["id"])

    # 偏航必须写进游标封批台账，且当天封口
    rows = _ledger(c, h, batch["id"])
    assert rows, "拒写未落游标封批台账"
    row = rows[0]
    assert row["decision"] == "拒写"
    assert row["deviation"] == "游标封批偏航"
    assert row["track"] == "甲"
    assert row["closed_at"], "台账未当天封口"
    assert row["created_at"][:10] == row["closed_at"][:10] == datetime.date.today().isoformat()

    gate = _gate_from_proof(c, h, batch["batch_code"])
    assert gate["formal_sealed"] is False
    assert gate["open_cursors"] == 1
    assert gate["consistent"] is True


# ---------------------------------------------------------------------------
# 失败测乙：草稿封批意图未晋升却算已封
# ---------------------------------------------------------------------------
def test_fail_b_draft_intent_is_not_counted_as_sealed():
    c = TestClient(app)
    h = _auth(c)
    batch, cursor = _open_cursor_batch(c, h, "TB-GATE-B-001")

    intent_res = c.post(
        "/api/seal-intents", headers=h, json={"batch_id": batch["id"]}
    )
    assert intent_res.status_code == 202, intent_res.text
    intent = intent_res.json()
    assert intent["status"] == "草稿"

    # 乙轨：先落草稿意图，批次正式零改，正式表零增
    _assert_no_formal_trace(batch["id"])
    proof = c.get(f"/api/proof/batch/{batch['batch_code']}", headers=h).json()
    assert proof["sealed"] is False, "草稿意图未晋升却算已封"
    assert proof["gate"]["pending_intent"] is True
    seals = c.get("/api/seals", headers=h).json()["items"]
    assert not [s for s in seals if s["batch_id"] == batch["id"]]

    # 两轨不得混用：草稿意图在案时甲轨直封也必须拒写
    crossed = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]})
    assert crossed.status_code == 409, crossed.text
    _assert_no_formal_trace(batch["id"])

    # 游标没收口就晋升：必须回滚意图，正式表仍零增
    bad = c.post(f"/api/seal-intents/{intent['id']}/promote", headers=h)
    assert bad.status_code == 409, bad.text
    _assert_no_formal_trace(batch["id"])
    intents = c.get("/api/seal-intents", headers=h, params={"batch_id": batch["id"]}).json()["items"]
    assert intents[0]["status"] == "已回滚"
    led = _ledger(c, h, batch["id"])
    assert any(
        x["decision"] == "回滚" and x["deviation"] == "游标封批偏航" for x in led
    )

    # 收口后再落意图并原子晋升
    closed = c.post(
        "/api/excursions/close",
        headers=h,
        json={"cursor_id": cursor["id"], "note": "异常段复核收口"},
    )
    assert closed.status_code == 200, closed.text
    intent2 = c.post(
        "/api/seal-intents", headers=h, json={"batch_id": batch["id"]}
    ).json()
    # 游标已收口但草稿意图在案：甲轨直封仍属混轨，必须拒写
    mixed = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]})
    assert mixed.status_code == 409, mixed.text
    assert "两轨" in mixed.json()["detail"], mixed.text
    _assert_no_formal_trace(batch["id"])
    promoted = c.post(f"/api/seal-intents/{intent2['id']}/promote", headers=h)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "已封批"
    assert promoted.json()["generation"] == 2

    with connect() as conn:
        snaps = conn.execute(
            "SELECT * FROM seal_snapshots WHERE batch_id=?", (batch["id"],)
        ).fetchall()
        assert len(snaps) == 1
        assert snaps[0]["batch_generation"] == 2
        assert '"open_cursors": 0' in snaps[0]["manifest"]
        batch_row = conn.execute(
            "SELECT status,generation FROM temp_batches WHERE id=?", (batch["id"],)
        ).fetchone()
        assert batch_row["status"] == "已封批"
        assert batch_row["generation"] == 2

    # 五页同源：游标 / 封批 / 批次 / 温谱 / 对账 的 gate 必须逐字相同
    export = c.post(
        "/api/spectrum/export", headers=h, json={"batch_id": batch["id"]}
    )
    assert export.status_code == 200, export.text
    gates = [
        _gate_from_excursions(c, h, batch["id"]),
        _gate_from_seals(c, h, batch["id"]),
        _gate_from_batch(c, h, batch["id"]),
        _gate_from_spectrum(c, h, batch["id"]),
        _gate_from_proof(c, h, batch["batch_code"]),
    ]
    assert all(g == gates[0] for g in gates), f"五页账实不同源: {gates}"
    assert gates[0]["formal_sealed"] is True
    assert gates[0]["open_cursors"] == 0
    assert gates[0]["consistent"] is True


# ---------------------------------------------------------------------------
# 争用失败测：双人同游标抢封批
# ---------------------------------------------------------------------------
def test_fail_c_contention_records_trace_without_seal():
    c = TestClient(app)
    admin = _auth(c, "admin")
    ops = _auth(c, "ops")
    batch, _cursor = _open_cursor_batch(c, admin, "TB-GATE-C-001")
    key = f"cursor-seal:batch:{batch['id']}"

    # 第一窗先抢到游标锁（模拟封批处理中）
    held = c.post("/api/locks/acquire", headers=admin, json={"lock_key": key})
    assert held.status_code == 200, held.text

    # 第二窗后到：只记争用，不落封批
    late_seal = c.post("/api/seals", headers=ops, json={"batch_id": batch["id"]})
    assert late_seal.status_code == 409, late_seal.text
    assert "争用" in late_seal.json()["detail"]
    late_intent = c.post("/api/seal-intents", headers=ops, json={"batch_id": batch["id"]})
    assert late_intent.status_code == 409, late_intent.text
    assert "争用" in late_intent.json()["detail"]

    _assert_no_formal_trace(batch["id"])
    intents = c.get(
        "/api/seal-intents", headers=ops, params={"batch_id": batch["id"]}
    ).json()["items"]
    assert intents == []
    cont = c.get("/api/contention", headers=ops).json()["items"]
    assert any(
        x["lock_key"] == key and x["actor"] == "ops" and x["action"] == "争用"
        for x in cont
    ), "后到争用痕未落库"
    assert c.post(
        "/api/locks/release", headers=admin, json={"lock_key": key}
    ).status_code == 200

    # 另开两窗（两个客户端 + 两线程）同游标抢封批：至多一人落意图
    barrier = threading.Barrier(2)
    results: list[int] = []
    lock = threading.Lock()

    def _try(client: TestClient, headers: dict) -> None:
        barrier.wait(timeout=10)
        resp = client.post(
            "/api/seal-intents", headers=headers, json={"batch_id": batch["id"]}
        )
        with lock:
            results.append(resp.status_code)

    win1, win2 = TestClient(app), TestClient(app)
    t1 = threading.Thread(target=_try, args=(win1, admin))
    t2 = threading.Thread(target=_try, args=(win2, ops))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert len(results) == 2
    assert results.count(202) <= 1, f"双窗同游标都落了封批意图: {results}"
    assert 409 in results, f"后到窗未留争用/拒绝: {results}"
    intents = c.get(
        "/api/seal-intents", headers=admin, params={"batch_id": batch["id"]}
    ).json()["items"]
    assert len(intents) <= 1
    cont_after = c.get("/api/contention", headers=admin).json()["items"]
    assert any(
        x["lock_key"] == key and x["action"] == "争用" for x in cont_after
    ), "双窗抢封批的争用痕未落库"
    _assert_no_formal_trace(batch["id"])
