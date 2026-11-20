from pydantic import BaseModel,Field
from typing import Any
class SchedOrderInput(BaseModel):
 name:str=Field(min_length=2,max_length=120)
 idempotency_key:str=Field(min_length=4,max_length=120)
 policy_id:int
 batches:list[dict[str,Any]]=Field(min_length=1)
class LineShiftInput(BaseModel):
 code:str=Field(min_length=1,max_length=60)
 name:str=Field(min_length=1,max_length=120)
 capacity:int=Field(ge=0)
class CapacityAdjustInput(BaseModel):
 delta:int
class SchedBatchInput(BaseModel):
 data:dict[str,Any]
