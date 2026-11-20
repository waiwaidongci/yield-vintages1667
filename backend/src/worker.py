import time,json,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run
from src.models.run import Run
from src.services.calculation import calculate
from src.services.delivery import tick
from src.services import scheduling
Base.metadata.create_all(engine)
r=redis.Redis.from_url(REDIS_URL)
while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=r.lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try: job.result=calculate(job.snapshot['sources'],job.snapshot['policy']);job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  # 排产队列：接单不阻塞，求解在此异步执行；租约回收保证中断可恢复
  with Session() as db: scheduling.recover_stale_leases(db)
  job_id=r.lpop(scheduling.QUEUE_KEY)
  if job_id:
   try: scheduling.process_one(int(job_id))
   except Exception: traceback.print_exc()
  with Session() as db: tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)
