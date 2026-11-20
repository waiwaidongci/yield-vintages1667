import json,random
from src.config.database import Base,engine,Session
from src.config.settings import CONFIG
from src.models.identity import User
from src.models.source import Source,Policy,Period
from src.models import run
from src.models.schedule import LineShift,SchedBatch
from src.utils.security import password_hash
from src.services.validation import validate_source
from src.services.sched_validation import validate_line_shift,validate_batch

def demo_rows():
 # 20 个历史生产日 × 3 试制线 × 2 班次 × 2 条 = 240；保证每天每线班都有底稿，
 # 否则“同一方案放到每个可排线班都有底稿的历史生产日”将无可用日期。
 rng=random.Random(42);rows=[]
 for day in range(20):
  period='2026-11-'+str(day+1).zfill(2)
  for department in ('试制线A','试制线B','试制线C'):
   for shift in ('早班','晚班'):
    for _ in range(2):
     n=rng.randint(100,1200);fp=int(n*rng.uniform(.7,.96));rw=rng.randint(0,n-fp)
     rows.append({'period':period,'department':department,'shift':shift,'input':n,'first_pass':fp,'reworked_good':rw,'scrap':n-fp-rw})
 return rows

def demo_line_shifts():
 base=[('试制线A','早班'),('试制线A','晚班'),('试制线B','早班'),('试制线B','晚班'),('试制线C','早班'),('试制线C','晚班')]
 return [{'code':d+'|'+s,'name':d+s,'capacity':2000} for d,s in base]

def demo_batches():
 # 每批不可拆；可加工线班为 6 个线班中的子集；数量刻意逼紧余量以暴露冲突场景
 return [
  {'key':'TP-2611-01','name':'阀体试制批','quantity':900,'eligible_shift_ids':[1,2,3]},
  {'key':'TP-2611-02','name':'端盖试制批','quantity':700,'eligible_shift_ids':[2,4,6]},
  {'key':'TP-2611-03','name':'阀芯试制批','quantity':600,'eligible_shift_ids':[1,3,5]},
  {'key':'TP-2611-04','name':'密封件试制批','quantity':500,'eligible_shift_ids':[4,5,6]},
 ]

def seed():
 Base.metadata.create_all(engine)
 with Session() as db:
  for role in ('admin','analyst','viewer'):
   if not db.query(User).filter_by(email=role+'@demo.local').first():db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
  rows=demo_rows()
  for name in sorted(set(r['period'] for r in rows)):
   if not db.query(Period).filter_by(name=name).first():db.add(Period(name=name,description='合成演示业务期间，seed=42'))
  for i,data in enumerate(rows):
   validate_source(data);key='demo-'+str(i+1)
   if not db.query(Source).filter_by(natural_key=key).first():db.add(Source(natural_key=key,data=data))
  if not db.query(Policy).filter_by(version=CONFIG['policy']['version']).first():db.add(Policy(version=CONFIG['policy']['version'],data=CONFIG['policy']))
  # 排产主数据：线班（编码=试制线|班次，与底稿聚合键对齐）与批次，自然键幂等
  for data in demo_line_shifts():
   validate_line_shift(data)
   if not db.query(LineShift).filter_by(code=data['code']).first():db.add(LineShift(code=data['code'],name=data['name'],capacity=data['capacity'],version=1,enabled=1))
  db.flush()
  for data in demo_batches():
   validate_batch(data)
   if not db.query(SchedBatch).filter_by(natural_key=data['key']).first():db.add(SchedBatch(natural_key=data['key'],data=data))
  db.commit();print(json.dumps({'users':db.query(User).count(),'sources':db.query(Source).count(),'policies':db.query(Policy).count(),'periods':db.query(Period).count(),'line_shifts':db.query(LineShift).count(),'sched_batches':db.query(SchedBatch).count()},ensure_ascii=False))
if __name__=='__main__':seed()
