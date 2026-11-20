"""试制排产应用服务：接单封存、异步求解、整份方案原子生效、并发退回重排。

关键约定：
- 接单即封存所选底稿（历史良率矩阵）、口径与各线班班余量，写 SHA256；
- 求解期间不锁库、不占产能，照常接单；
- 仅 OPTIMAL 方案进入提交事务：行锁复检余量版本，整份方案与产能扣减同事务生效；
- 余量已变（版本不符或余量不足）→ 整单退回，按最新余量重排（次数留痕、有上限）；
- 租约超时可由其他 worker 回收；幂等键保证重试同一排产单不重复占用产能。
"""
import hashlib,json,time,socket,os
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm.attributes import flag_modified
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.source import Source,Policy
from src.models.schedule import LineShift,SchedBatch,SchedOrder,SchedAssignment,SchedLeaseLog
from src.services.audit import log
from src.services.sched_validation import validate_batch,validate_line_shift
from src.services import solver
import redis
QUEUE_KEY='sched-jobs'
LEASE_SECONDS=60.0
MAX_ATTEMPTS=5
WORKER_ID=socket.gethostname()+'-'+str(os.getpid())
def _rds(): return redis.Redis.from_url(REDIS_URL)
# ---------- 历史良率矩阵（由封存底稿按封存口径聚合，按分母加权，绝不使用批次率简单平均） ----------
def history_matrix(db,shift_map,policy=None):
 use_total_good=(not policy) or policy.get('numerator')=='total_good'
 deduct_scrap=bool(policy) and policy.get('denominator')=='non_scrap'
 rows=db.query(Source).order_by(Source.id).all()
 agg={}
 for r in rows:
  d=r.data;key=(d['period'],d['department'],d['shift'])
  num=d['first_pass']+(d['reworked_good'] if use_total_good else 0)
  den=d['input']-(d['scrap'] if deduct_scrap else 0)
  n0,d0=agg.get(key,(0,0))
  agg[key]=(n0+num,d0+den)
 matrix={}
 for (period,dept,shift),(num,den) in agg.items():
  code=dept+'|'+shift
  sid=shift_map.get(code)
  if sid is None or den<=0: continue
  matrix.setdefault(period,{})[sid]=num/den
 return matrix
def _shift_map(db):
 return {s.code:s.id for s in db.query(LineShift).filter_by(enabled=1).all()}
def _snapshot(db,batches,policy):
 shifts=db.query(LineShift).filter_by(enabled=1).order_by(LineShift.id).all()
 shift_ids={s.id for s in shifts}
 for b in batches:
  unknown=[sid for sid in b['eligible_shift_ids'] if sid not in shift_ids]
  if unknown: raise HTTPException(422,'批次 %s 引用了不存在或停用的线班 %s'%(b['key'],unknown))
 smap={s.code:s.id for s in shifts}
 history=history_matrix(db,smap,policy.data)
 # 至少要有一个历史日覆盖全部可排线班，否则无法做 maximin 保底
 covered=[day for day,rates in history.items() if all(s.id in rates for s in shifts)]
 if not covered: raise HTTPException(422,'没有任何历史生产日同时具备全部线班底稿，无法估算保底件数')
 return {'sealed_at':time.time(),'policy_basis':{'version':policy.data['version'],'formula':'加权良率=(一次通过+返工合格)合计/投入合计；同日各线班按投入加权估算合格件数'},'shifts':[{'id':s.id,'code':s.code,'name':s.name,'capacity_sealed':s.capacity,'version':s.version} for s in shifts],'batches':batches,'history':history}
def checksum_of(snapshot):
 return hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
# ---------- 接单 ----------
def create_order(db,user,name,idempotency_key,policy_id,batch_payloads):
 text_key=idempotency_key.strip() if isinstance(idempotency_key,str) else ''
 if not text_key: raise HTTPException(422,'幂等键不能为空')
 existed=db.query(SchedOrder).filter_by(idempotency_key=text_key).first()
 if existed: return existed,True
 policy=db.get(Policy,policy_id)
 if not policy: raise HTTPException(404,'口径版本不存在')
 if not batch_payloads: raise HTTPException(422,'至少提交一个批次')
 keys=set()
 for b in batch_payloads:
  validate_batch(b)
  if b['key'] in keys: raise HTTPException(422,'批次编号重复：%s'%b['key'])
  keys.add(b['key'])
 snapshot=_snapshot(db,batch_payloads,policy)
 obj=SchedOrder(idempotency_key=text_key,name=name.strip(),status='queued',attempts=0,snapshot=snapshot,checksum=checksum_of(snapshot),capacity_version=max(s['version'] for s in snapshot['shifts']),created=time.time(),updated=time.time(),created_by=user.email)
 db.add(obj);db.flush()
 db.add(SchedLeaseLog(order_id=obj.id,event='created',worker=user.email,at=time.time(),detail='接单封存底稿/口径/班余量，checksum='+obj.checksum))
 log(db,user.email,'sched.create',obj.id);db.commit()
 _rds().rpush(QUEUE_KEY,obj.id)
 return obj,False
# ---------- worker 租约与回收 ----------
def recover_stale_leases(db):
 stale=db.query(SchedOrder).filter(SchedOrder.status=='running',SchedOrder.leased_at.isnot(None)).all()
 now=time.time();recovered=[]
 for o in stale:
  if now-(o.leased_at or 0)>LEASE_SECONDS:
   o.status='queued';o.leased_by=None;o.leased_at=None;o.updated=now
   db.add(SchedLeaseLog(order_id=o.id,event='lease-recover',worker=WORKER_ID,at=now,detail='租约超时，中断可恢复，重新入队'))
   recovered.append(o.id)
 if recovered:
  db.commit()
  # 回收后必须重新投递队列，否则排产单会滞留 queued 无人消费
  for oid in recovered: _rds().rpush(QUEUE_KEY,oid)
 return len(recovered)
def _lease(db,oid):
 # 条件更新抢占：即使多个 worker 同时取到同一消息，也只有一个能把 queued→running
 now=time.time()
 res=db.execute(update(SchedOrder).where(SchedOrder.id==oid,SchedOrder.status=='queued').values(status='running',attempts=SchedOrder.attempts+1,leased_by=WORKER_ID,leased_at=now,updated=now))
 if res.rowcount==0: db.rollback();return None
 obj=db.get(SchedOrder,oid)
 db.add(SchedLeaseLog(order_id=oid,event='lease',worker=WORKER_ID,at=now,detail='第%s次求解尝试'%obj.attempts))
 db.commit();return obj
def _release_back_to_queue(db,obj,event,detail):
 obj.status='queued';obj.leased_by=None;obj.leased_at=None;obj.reject_reason=detail;obj.updated=time.time()
 db.add(SchedLeaseLog(order_id=obj.id,event=event,worker=WORKER_ID,at=time.time(),detail=detail))
 db.commit();_rds().rpush(QUEUE_KEY,obj.id)
def _refresh_capacity(db,obj):
 """退回重排时保留原始封存，仅把班余量刷新为当前值并留痕。"""
 current={s.id:s for s in db.query(LineShift).all()}
 original={s['id']:dict(s) for s in obj.snapshot['shifts']}
 for s in obj.snapshot['shifts']:
  fresh=current.get(s['id'])
  if fresh: s['capacity_sealed']=fresh.capacity;s['version']=fresh.version
 obj.snapshot.setdefault('capacity_reseals',[]).append({'at':time.time(),'from':original,'worker':WORKER_ID})
 obj.checksum=checksum_of(obj.snapshot)
 flag_modified(obj,'snapshot')
# ---------- 求解 + 原子提交 ----------
def process_one(oid):
 with Session() as db:
  recover_stale_leases(db)
  obj=_lease(db,oid)
  if not obj: return 'skip'
  try:
   status_name,payload=solver.solve(obj.snapshot)
  except Exception as exc:
   obj.status='failed';obj.error='求解异常：%s'%exc;obj.leased_by=None;obj.updated=time.time()
   db.add(SchedLeaseLog(order_id=obj.id,event='solve-error',worker=WORKER_ID,at=time.time(),detail=str(exc)))
   db.commit();return 'failed'
  if status_name!='OPTIMAL':
   # 未证最优/不可行：不占任何产能；可自动重试的仅限求解器超时类，且次数有上限
   obj.result=payload;obj.reject_reason=payload['reason'];obj.leased_by=None;obj.updated=time.time()
   db.add(SchedLeaseLog(order_id=obj.id,event='reject-no-optimal',worker=WORKER_ID,at=time.time(),detail=payload['reason']))
   if status_name in ('FEASIBLE','UNKNOWN') and obj.attempts<MAX_ATTEMPTS:
    db.commit();_release_back_to_queue(db,obj,'retry-timeout',payload['reason']);return 'requeued'
   obj.status='rejected';db.commit();return 'rejected'
  return _commit_optimal(db,obj,payload)
def _commit_optimal(db,obj,payload):
 """行锁复检余量 → 整份方案 + 产能扣减同事务生效。"""
 now=time.time()
 ids=[s['id'] for s in obj.snapshot['shifts']]
 locked={s.id:s for s in db.query(LineShift).filter(LineShift.id.in_(ids)).with_for_update().all()}
 need={}
 for a in payload['assignments']: need[a['line_shift_id']]=need.get(a['line_shift_id'],0)+a['quantity']
 conflicts=[]
 for sid,qty in need.items():
  s=locked.get(sid)
  sealed=next(x for x in obj.snapshot['shifts'] if x['id']==sid)
  if s is None: conflicts.append('线班 %s 已不存在'%sid)
  elif s.version!=sealed['version']: conflicts.append('线班 %s 余量已变（版本 %s→%s），整单退回重排'%(s.code,sealed['version'],s.version))
  elif s.capacity<qty: conflicts.append('线班 %s 当前余量 %s 小于需占用 %s，整单退回重排'%(s.code,s.capacity,qty))
 if conflicts:
  detail='；'.join(conflicts)
  obj.result=payload;obj.reject_reason=detail;obj.leased_by=None;obj.updated=now
  db.add(SchedLeaseLog(order_id=obj.id,event='capacity-conflict',worker=WORKER_ID,at=now,detail=detail))
  if obj.attempts<MAX_ATTEMPTS:
   db.commit();_refresh_capacity(db,obj);_release_back_to_queue(db,obj,'replan',detail);return 'replanned'
  obj.status='rejected';db.commit();return 'rejected'
 # 全部通过：扣减、版本推进、写安排，同一事务
 for sid,qty in need.items():
  locked[sid].capacity-=qty;locked[sid].version+=1
 for a in payload['assignments']:
  db.add(SchedAssignment(order_id=obj.id,batch_key=a['batch_key'],line_shift_id=a['line_shift_id'],line_shift_code=a['line_shift_code'],quantity=a['quantity'],est_good_floor=payload['floor_good_pieces']))
 obj.status='completed';obj.result=payload;obj.error=None;obj.reject_reason=None
 obj.capacity_version=max(s.version for s in locked.values());obj.leased_by=None;obj.updated=now
 db.add(SchedLeaseLog(order_id=obj.id,event='commit',worker=WORKER_ID,at=now,detail='方案生效，保底件数=%s'%payload['floor_good_pieces']))
 log(db,obj.created_by or WORKER_ID,'sched.complete',obj.id)
 db.commit();return 'completed'
# ---------- 人工重试（同一排产单，不重复占用：未完成单无任何扣减记录） ----------
def retry_order(db,user,oid):
 obj=db.get(SchedOrder,oid)
 if not obj: raise HTTPException(404,'排产单不存在')
 if obj.status=='completed': raise HTTPException(409,'排产单已生效，无需重试；重试不会重复占用产能')
 if obj.status in ('queued','running'): raise HTTPException(409,'排产单仍在队列中，中断恢复由系统自动完成')
 if obj.attempts>=MAX_ATTEMPTS:
  _refresh_capacity(db,obj);obj.attempts=0
 obj.status='queued';obj.leased_by=None;obj.leased_at=None;obj.updated=time.time()
 db.add(SchedLeaseLog(order_id=oid,event='manual-retry',worker=user.email,at=time.time(),detail='人工重试同一排产单'))
 log(db,user.email,'sched.retry',oid);db.commit()
 _rds().rpush(QUEUE_KEY,oid);return obj
# ---------- 线班与批次主数据 ----------
def upsert_line_shift(db,user,data):
 validate_line_shift(data)
 obj=db.query(LineShift).filter_by(code=data['code']).first()
 if obj:
  if obj.capacity!=data['capacity']: obj.version+=1
  obj.name=data['name'];obj.capacity=data['capacity']
 else: obj=LineShift(code=data['code'],name=data['name'],capacity=data['capacity'],version=1,enabled=1);db.add(obj)
 log(db,user.email,'sched.shift.upsert',data['code']);db.commit();return obj
def adjust_capacity(db,user,sid,delta):
 obj=db.get(LineShift,sid)
 if not obj: raise HTTPException(404,'线班不存在')
 if obj.capacity+delta<0: raise HTTPException(422,'调整后余量不能为负')
 obj.capacity+=delta;obj.version+=1
 log(db,user.email,'sched.shift.adjust','%s delta=%s v%s'%(obj.code,delta,obj.version));db.commit();return obj
def upsert_batch(db,user,data):
 validate_batch(data)
 obj=db.query(SchedBatch).filter_by(natural_key=data['key']).first()
 if obj: obj.data=data
 else: obj=SchedBatch(natural_key=data['key'],data=data);db.add(obj)
 log(db,user.email,'sched.batch.upsert',data['key']);db.commit();return obj
