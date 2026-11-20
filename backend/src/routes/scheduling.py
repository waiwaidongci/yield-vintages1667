from fastapi import APIRouter,Depends,HTTPException
from src.config.database import database
from src.controllers.auth import current,editor,admin
from src.models.schedule import LineShift,SchedBatch,SchedOrder,SchedAssignment,SchedLeaseLog
from src.models.source import Policy
from src.schemas.sched_requests import SchedOrderInput,LineShiftInput,CapacityAdjustInput,SchedBatchInput
from src.services import scheduling
from src.utils.serialize import row
router=APIRouter(prefix='/scheduling')
@router.get('/line-shifts')
def shifts(db=Depends(database),u=Depends(current)):
 return [row(x) for x in db.query(LineShift).order_by(LineShift.id).all()]
@router.post('/line-shifts')
def shift_create(b:LineShiftInput,db=Depends(database),u=Depends(admin)):
 return row(scheduling.upsert_line_shift(db,u,b.model_dump()))
@router.post('/line-shifts/{id}/adjust')
def shift_adjust(id:int,b:CapacityAdjustInput,db=Depends(database),u=Depends(admin)):
 return row(scheduling.adjust_capacity(db,u,id,b.delta))
@router.get('/batches')
def batches(db=Depends(database),u=Depends(current)):
 return [row(x) for x in db.query(SchedBatch).order_by(SchedBatch.id).all()]
@router.post('/batches')
def batch_create(b:SchedBatchInput,db=Depends(database),u=Depends(editor)):
 return row(scheduling.upsert_batch(db,u,b.data))
@router.get('/orders')
def orders(db=Depends(database),u=Depends(current)):
 out=[]
 for o in db.query(SchedOrder).order_by(SchedOrder.id.desc()).all():
  d=row(o);d['assignments']=[row(a) for a in db.query(SchedAssignment).filter_by(order_id=o.id).all()]
  out.append(d)
 return out
@router.get('/orders/{id}')
def order_detail(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(SchedOrder,id)
 if not obj: raise HTTPException(404,'排产单不存在')
 d=row(obj);d['assignments']=[row(a) for a in db.query(SchedAssignment).filter_by(order_id=id).all()]
 d['events']=[row(x) for x in db.query(SchedLeaseLog).filter_by(order_id=id).order_by(SchedLeaseLog.id).all()]
 return d
@router.post('/orders')
def order_create(b:SchedOrderInput,db=Depends(database),u=Depends(editor)):
 obj,dedup=scheduling.create_order(db,u,b.name,b.idempotency_key,b.policy_id,b.batches)
 d=row(obj);d['dedup_hit']=dedup
 return d
@router.post('/orders/{id}/retry')
def order_retry(id:int,db=Depends(database),u=Depends(editor)):
 return row(scheduling.retry_order(db,u,id))
@router.get('/history-yield')
def history(policy_id:int|None=None,db=Depends(database),u=Depends(current)):
 """供页面查看：封存口径下每个历史生产日各线班的加权良率。"""
 smap=scheduling._shift_map(db)
 policy=db.get(Policy,policy_id) if policy_id else db.query(Policy).order_by(Policy.id).first()
 return scheduling.history_matrix(db,smap,policy.data if policy else None)
