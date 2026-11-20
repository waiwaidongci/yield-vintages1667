import os, sys, random, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix='.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + _DB_PATH)
os.environ.setdefault('REDIS_URL', 'redis://localhost:6399/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')
import pytest
import fakeredis
from src.config.database import Base, engine, Session
from src.models import identity, source, run, schedule as _sched
from src.models.identity import User
from src.models.source import Source, Policy
from src.models.schedule import LineShift, SchedBatch
from src.utils.security import password_hash
from src.services import scheduling

@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis()
    # 所有队列操作复用同一客户端，保证跨函数共享同一份内存数据
    monkeypatch.setattr(scheduling, '_rds', lambda: client)
    monkeypatch.setattr(scheduling.redis, 'Redis', fakeredis.FakeRedis)
    return client

@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as session:
        yield session

@pytest.fixture
def seeded(db):
    for role in ('admin', 'analyst', 'viewer'):
        db.add(User(email=role + '@demo.local', role=role, password=password_hash('Demo2026!')))
    pol = Policy(version='v1', data={'version': 'v1', 'numerator': 'total_good', 'denominator': 'input', 'description': '测试口径'})
    db.add(pol)
    shifts = []
    for d in ('试制线A', '试制线B', '试制线C'):
        for sh in ('早班', '晚班'):
            s = LineShift(code=d + '|' + sh, name=d + sh, capacity=1500, version=1, enabled=1)
            db.add(s); shifts.append(s)
    db.flush()
    rng = random.Random(7)
    for day in range(1, 11):
        period = '2026-11-%02d' % day
        for s in shifts:
            d, sh = s.code.split('|')
            for k in range(2):
                n = rng.randint(800, 1200)
                good = int(n * rng.uniform(0.7, 0.98)); rw = min(int((n - good) * 0.5), good)
                db.add(Source(natural_key='h-%s-%s-%s' % (day, s.code, k),
                              data={'period': period, 'department': d, 'shift': sh, 'input': n,
                                    'first_pass': good - rw, 'reworked_good': rw, 'scrap': n - good}))
    db.commit()
    return {'db': db, 'policy': pol, 'shifts': shifts}

@pytest.fixture
def batches(seeded):
    shifts = seeded['shifts']
    return [
        {'key': 'B1', 'name': '批1', 'quantity': 800, 'eligible_shift_ids': [shifts[0].id, shifts[2].id, shifts[4].id]},
        {'key': 'B2', 'name': '批2', 'quantity': 600, 'eligible_shift_ids': [shifts[1].id, shifts[3].id, shifts[5].id]},
        {'key': 'B3', 'name': '批3', 'quantity': 400, 'eligible_shift_ids': [s.id for s in shifts]},
    ]

class _User:
    def __init__(self, email, role):
        self.email = email; self.role = role
