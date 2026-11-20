import time, json
import pytest
from src.config.database import Session
from src.models.schedule import SchedOrder, SchedAssignment, LineShift, SchedLeaseLog
from src.models.source import Policy
from src.services import scheduling, solver
from conftest import _User

ADMIN = _User('admin@demo.local', 'admin')

def _make_order(seeded, batches, key='idem-001', name='十一月试制排产'):
    db = seeded['db']
    obj, dedup = scheduling.create_order(db, ADMIN, name, key, seeded['policy'].id, batches)
    return obj

def test_create_seals_snapshot_and_checksum(seeded, batches):
    obj = _make_order(seeded, batches)
    assert obj.status == 'queued' and obj.attempts == 0
    assert obj.checksum and len(obj.checksum) == 64
    snap = obj.snapshot
    assert len(snap['shifts']) == 6
    assert all('capacity_sealed' in s and s['capacity_sealed'] == 1500 for s in snap['shifts'])
    assert len(snap['history']) == 10
    assert snap['policy_basis']['version'] == 'v1'
    # 封存后再改线班余量，不影响已封存快照
    db = seeded['db']
    s = db.query(LineShift).first()
    s.capacity = 10; s.version += 1; db.commit()
    again = db.get(SchedOrder, obj.id)
    assert again.snapshot['shifts'][0]['capacity_sealed'] == 1500

def test_idempotent_retry_does_not_duplicate(seeded, batches):
    db = seeded['db']
    o1, d1 = scheduling.create_order(db, ADMIN, '排产', 'same-key', seeded['policy'].id, batches)
    o2, d2 = scheduling.create_order(db, ADMIN, '排产', 'same-key', seeded['policy'].id, batches)
    assert o1.id == o2.id and d1 is False and d2 is True
    assert db.query(SchedOrder).count() == 1

def test_full_run_completes_and_deducts_capacity(seeded, batches, fake_redis):
    obj = _make_order(seeded, batches)
    outcome = scheduling.process_one(obj.id)
    assert outcome == 'completed'
    with Session() as db:
        order = db.get(SchedOrder, obj.id)
        assert order.status == 'completed'
        assigns = db.query(SchedAssignment).filter_by(order_id=order.id).all()
        assert len(assigns) == 3
        assert sum(a.quantity for a in assigns) == 1800
        # 整份方案与扣减已生效；被占用线班版本 +1、余量下降
        for a in assigns:
            s = db.get(LineShift, a.line_shift_id)
            assert s.version == 2
        load = {}
        for a in assigns:
            load[a.line_shift_id] = load.get(a.line_shift_id, 0) + a.quantity
        for sid, q in load.items():
            s = db.get(LineShift, sid)
            assert s.capacity == 1500 - q
        # 保底件数与逐日复算一致
        floor = order.result['floor_good_pieces']
        assert floor == min(int(v) for v in order.result['per_day_good'].values())
        assert all(a.est_good_floor == floor for a in assigns)

def test_concurrent_capacity_change_replans_then_commits(seeded, batches, fake_redis, monkeypatch):
    obj = _make_order(seeded, batches, key='idem-concurrent')
    # 模拟另一笔并发排产在求解期间改动了余量（版本推进）
    with Session() as db:
        target = db.query(LineShift).filter_by(code='试制线C|早班').first()
        target.capacity -= 100; target.version += 1; db.commit()
    outcome = scheduling.process_one(obj.id)
    # 首次提交撞版本 → 退回重排（requeued），且当时没有任何扣减/安排落库
    assert outcome == 'replanned'
    with Session() as db:
        assert db.query(SchedAssignment).filter_by(order_id=obj.id).count() == 0
        order = db.get(SchedOrder, obj.id)
        assert order.status == 'queued'
        assert '退回重排' in order.reject_reason
        # 重排快照已刷新到新版本
        c = next(s for s in order.snapshot['shifts'] if s['code'] == '试制线C|早班')
        assert c['capacity_sealed'] == 1400 and c['version'] == 2
    # 再次求解：按最新余量提交成功
    outcome2 = scheduling.process_one(obj.id)
    assert outcome2 == 'completed'
    with Session() as db:
        order = db.get(SchedOrder, obj.id)
        assert order.status == 'completed'
        events = [e.event for e in db.query(SchedLeaseLog).filter_by(order_id=obj.id).all()]
        assert 'capacity-conflict' in events and 'commit' in events

def test_capacity_insufficient_rejected_after_max_attempts(seeded, batches, fake_redis):
    obj = _make_order(seeded, batches, key='idem-tight')
    # 把所有线班余量压到无法承接任何批次
    with Session() as db:
        for s in db.query(LineShift).all():
            s.capacity = 10; s.version += 1
        db.commit()
    scheduling.MAX_ATTEMPTS = 1
    try:
        outcome = scheduling.process_one(obj.id)
    finally:
        scheduling.MAX_ATTEMPTS = 5
    assert outcome == 'rejected'
    with Session() as db:
        order = db.get(SchedOrder, obj.id)
        assert order.status == 'rejected'
        assert order.reject_reason and '退回重排' in order.reject_reason
        assert db.query(SchedAssignment).filter_by(order_id=obj.id).count() == 0
        # 被拒单不产生任何扣减
        assert all(s.capacity == 10 for s in db.query(LineShift).all())

def test_stale_lease_is_recovered(seeded, batches, fake_redis):
    obj = _make_order(seeded, batches, key='idem-lease')
    with Session() as db:
        o = db.get(SchedOrder, obj.id)
        o.status = 'running'; o.leased_by = 'dead-worker'; o.leased_at = time.time() - 120
        db.commit()
    # 队列里的原始消息已被取走，模拟 worker 崩溃
    fake_redis.lpop(scheduling.QUEUE_KEY)
    with Session() as db:
        n = scheduling.recover_stale_leases(db)
        assert n == 1
        o = db.get(SchedOrder, obj.id)
        assert o.status == 'queued' and o.leased_by is None
    # 回收后重新入队，可被再次消费直至生效
    assert int(fake_redis.lpop(scheduling.QUEUE_KEY)) == obj.id
    assert scheduling.process_one(obj.id) == 'completed'

def test_line_shift_upsert_bumps_version_on_capacity_change(seeded):
    from src.services.sched_validation import validate_line_shift
    with Session() as db:
        s = scheduling.upsert_line_shift(db, ADMIN, {'code': '试制线A|早班', 'name': '试制线A早班', 'capacity': 999})
        assert s.capacity == 999 and s.version == 2
        s2 = scheduling.upsert_line_shift(db, ADMIN, {'code': '试制线A|早班', 'name': '试制线A早班', 'capacity': 999})
        assert s2.version == 2  # 余量未变，版本不推进
        s3 = scheduling.upsert_line_shift(db, ADMIN, {'code': '试制线A|早班', 'name': '试制线A早班', 'capacity': 888})
        assert s3.version == 3

def test_manual_retry_same_order_no_double_occupancy(seeded, batches, fake_redis):
    obj = _make_order(seeded, batches, key='idem-retry')
    scheduling.process_one(obj.id)  # 已完成
    with pytest.raises(Exception):
        scheduling.retry_order(seeded['db'], ADMIN, obj.id)
    # 完成态重试被拒；安排只有一份，未重复占用
    with Session() as db:
        assert db.query(SchedAssignment).filter_by(order_id=obj.id).count() == 3

def test_infeasible_batch_rejected_without_capacity_use(seeded, fake_redis):
    db = seeded['db']
    bad = [{'key': 'X1', 'name': '超大批', 'quantity': 99999, 'eligible_shift_ids': [seeded['shifts'][0].id]}]
    obj, _ = scheduling.create_order(db, ADMIN, '不可行单', 'idem-infeasible', seeded['policy'].id, bad)
    scheduling.MAX_ATTEMPTS = 1
    try:
        outcome = scheduling.process_one(obj.id)
    finally:
        scheduling.MAX_ATTEMPTS = 5
    assert outcome == 'rejected'
    with Session() as db:
        order = db.get(SchedOrder, obj.id)
        assert order.status == 'rejected' and '无可行' in order.reject_reason
        assert db.query(SchedAssignment).filter_by(order_id=obj.id).count() == 0

def test_timeout_not_optimal_does_not_occupy(seeded, batches, monkeypatch, fake_redis):
    obj = _make_order(seeded, batches, key='idem-timeout')
    class _Result:
        pass
    def fake_solve(snapshot):
        return 'FEASIBLE', {'reason': '30秒内仅得可行解、未证最优，按规则不占用产能', 'elapsed_seconds': 30.0,
                            'days_evaluated': [], 'shift_count': 6, 'batch_count': 3}
    monkeypatch.setattr(scheduling, 'solver', type('M', (), {'solve': staticmethod(fake_solve)}))
    scheduling.MAX_ATTEMPTS = 1
    try:
        outcome = scheduling.process_one(obj.id)
    finally:
        scheduling.MAX_ATTEMPTS = 5
    assert outcome == 'rejected'
    with Session() as db:
        assert db.query(SchedAssignment).filter_by(order_id=obj.id).count() == 0
        assert all(s.capacity == 1500 for s in db.query(LineShift).all())

def test_batch_unsplittable_and_eligible_only(seeded):
    # 批次只能分给声明的可加工线班；求解结果不得越界
    shifts = seeded['shifts']
    batches = [{'key': 'S1', 'name': '专线条', 'quantity': 500, 'eligible_shift_ids': [shifts[0].id]}]
    snap = scheduling._snapshot(seeded['db'], batches, seeded['policy'])
    status, payload = solver.solve(snap)
    assert status == 'OPTIMAL'
    assert len(payload['assignments']) == 1
    assert payload['assignments'][0]['line_shift_id'] == shifts[0].id

def test_validation_rejects_bad_payload(seeded):
    from src.services.sched_validation import validate_batch, validate_line_shift
    with pytest.raises(ValueError):
        validate_batch({'key': 'k', 'name': 'n', 'quantity': 1.5, 'eligible_shift_ids': [1]})
    with pytest.raises(ValueError):
        validate_batch({'key': 'k', 'name': 'n', 'quantity': 1, 'eligible_shift_ids': []})
    with pytest.raises(ValueError):
        validate_line_shift({'code': 'c', 'name': 'n', 'capacity': -1})
