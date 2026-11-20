from sqlalchemy import Column,Integer,String,JSON,Text,Float
from src.config.database import Base
class LineShift(Base):
 __tablename__='sched_line_shifts'
 id=Column(Integer,primary_key=True); code=Column(String(60),unique=True); name=Column(String(120)); capacity=Column(Integer,nullable=False); version=Column(Integer,default=1,nullable=False); enabled=Column(Integer,default=1)
class SchedBatch(Base):
 __tablename__='sched_batches'
 id=Column(Integer,primary_key=True); natural_key=Column(String(120),unique=True); data=Column(JSON,nullable=False)
class SchedOrder(Base):
 __tablename__='sched_orders'
 id=Column(Integer,primary_key=True); idempotency_key=Column(String(120),unique=True); name=Column(String(120)); status=Column(String(20),default='queued'); attempts=Column(Integer,default=0); snapshot=Column(JSON); checksum=Column(String(64)); result=Column(JSON); error=Column(Text); reject_reason=Column(Text); capacity_version=Column(Integer); leased_by=Column(String(120)); leased_at=Column(Float); created=Column(Float); updated=Column(Float); created_by=Column(String(120))
class SchedAssignment(Base):
 __tablename__='sched_assignments'
 id=Column(Integer,primary_key=True); order_id=Column(Integer,index=True); batch_key=Column(String(120)); line_shift_id=Column(Integer); line_shift_code=Column(String(60)); quantity=Column(Integer); est_good_floor=Column(Integer)
class SchedLeaseLog(Base):
 __tablename__='sched_lease_logs'
 id=Column(Integer,primary_key=True); order_id=Column(Integer); event=Column(String(40)); worker=Column(String(120)); at=Column(Float); detail=Column(Text)
