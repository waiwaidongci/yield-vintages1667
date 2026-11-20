"""试制排产求解器：在历史生产日上做 maximin 鲁棒优化。

方案 x[b,s]（批次 b 整批分给可加工线班 s，不可拆分）同时放到每一个
"所有可排线班都有底稿"的历史生产日 d，用同日各线班的加权良率估算
合格件数 good_d = Σ_s 投入_s × 当日线班良率_s，目标是让最差一天的
预计合格数（保底件数）最大。

良率按 RATE_SCALE 放大为整数进入 CP-SAT，杜绝浮点误差；
仅当求解状态为 OPTIMAL（30 秒内证得最优）才允许占用产能，
其余情况一律退回，不写任何余量。
"""
import time
from ortools.sat.python import cp_model
SOLVE_SECONDS=30.0
RATE_SCALE=1_000_000
# CP-SAT 终态：OPTIMAL 才可占用产能；FEASIBLE 属于未证最优
_STATUS_NAME={cp_model.OPTIMAL:'OPTIMAL',cp_model.FEASIBLE:'FEASIBLE',cp_model.INFEASIBLE:'INFEASIBLE',cp_model.MODEL_INVALID:'MODEL_INVALID',cp_model.UNKNOWN:'UNKNOWN'}
def build_context(snapshot):
 """由接单时封存的快照构建求解上下文：线班、批次、可排历史日、每日良率矩阵。"""
 shifts=snapshot['shifts']; batches=snapshot['batches']; history=snapshot['history']
 shift_ids=[s['id'] for s in shifts]
 shift_by_id={s['id']:s for s in shifts}
 eligible={b['key']:[sid for sid in b['eligible_shift_ids'] if sid in shift_by_id] for b in batches}
 # 快照经 JSON 落库后字典键会变成字符串，这里统一归一化为整数线班 id
 normalized={}
 for day,raw in history.items():
  rates={int(k):v for k,v in raw.items()}
  if all(sid in rates for sid in shift_ids):
   normalized[day]=rates
 # 每个历史日必须对方案涉及的全部可排线班都有底稿
 usable_days=sorted(normalized)
 if not usable_days:
  raise ValueError('没有任何历史生产日同时具备全部可排线班底稿，无法估算保底件数')
 rate_matrix={day:{sid:int(round(normalized[day][sid]*RATE_SCALE)) for sid in shift_ids} for day in usable_days}
 return shift_ids,eligible,usable_days,rate_matrix,normalized
def solve(snapshot):
 """返回 (status_name, payload)。payload 仅在 OPTIMAL 时含 assignments/floor。"""
 started=time.time()
 shift_ids,eligible,days,rate_matrix,history=build_context(snapshot)
 shifts=snapshot['shifts']
 batches=snapshot['batches']
 model=cp_model.CpModel()
 x={}
 for b in batches:
  choices=eligible[b['key']]
  if not choices:
   return 'INFEASIBLE',{'reason':'批次 %s 没有可加工线班'%b['key']}
  for sid in choices:
   x[(b['key'],sid)]=model.NewBoolVar('x_%s_%s'%(b['key'],sid))
  # 每批不可拆：恰好落到一个可加工线班
  model.AddExactlyOne(x[(b['key'],sid)] for sid in choices)
 # 不超余量：每个线班承接的整批数量之和 ≤ 接单封存余量
 for sid in shift_ids:
  cap=next(s['capacity_sealed'] for s in snapshot['shifts'] if s['id']==sid)
  model.Add(sum(b['quantity']*x[(b['key'],sid)] for b in batches if (b['key'],sid) in x)<=cap)
 # 每日加权良率估算合格数；floor 为最差一天的保底件数（整数件）
 floor=model.NewIntVar(0,sum(b['quantity'] for b in batches),'floor_good')
 day_exprs={}
 for day in days:
  terms=[]
  for b in batches:
   for sid in eligible[b['key']]:
    terms.append(b['quantity']*rate_matrix[day][sid]*x[(b['key'],sid)])
  # good_d = Σ 投入×良率，再除 RATE_SCALE；用 floor*SCALE <= Σ 表达保底下界
  day_exprs[day]=sum(terms)
  model.Add(floor*RATE_SCALE<=day_exprs[day])
 model.Maximize(floor)
 solver=cp_model.CpSolver()
 solver.parameters.max_time_in_seconds=SOLVE_SECONDS
 solver.parameters.num_search_workers=8
 solver.parameters.log_search_progress=False
 status=solver.Solve(model)
 name=_STATUS_NAME.get(status,'UNKNOWN')
 elapsed=round(time.time()-started,3)
 base={'elapsed_seconds':elapsed,'days_evaluated':days,'shift_count':len(shift_ids),'batch_count':len(batches)}
 if status!=cp_model.OPTIMAL:
  reason={'FEASIBLE':'30秒内仅得可行解、未证最优，按规则不占用产能','INFEASIBLE':'在封存余量与可加工线班约束下无可行排产方案','UNKNOWN':'30秒内求解器未给出结论，按规则不占用产能','MODEL_INVALID':'求解模型无效'}.get(name,'求解状态 %s，按规则不占用产能'%name)
  return name,{**base,'reason':reason}
 assignments=[]
 for b in batches:
  for sid in eligible[b['key']]:
   if solver.Value(x[(b['key'],sid)])==1:
    assignments.append({'batch_key':b['key'],'batch_name':b['name'],'line_shift_id':sid,'line_shift_code':next(s['code'] for s in shifts if s['id']==sid),'quantity':b['quantity']})
 # 逐日复算保底，给出每天的预计合格数（保留两位小数）与最差日
 per_day={}
 for day in days:
  total=0.0
  for a in assignments:
   total+=a['quantity']*history[day][a['line_shift_id']]
  per_day[day]=round(total,2)
 worst_day=min(per_day,key=lambda d:per_day[d])
 floor_pieces=min(int(per_day[d]) for d in days)
 payload={**base,'status':'OPTIMAL','objective_floor':solver.ObjectiveValue(),'floor_good_pieces':floor_pieces,'worst_day':worst_day,'per_day_good':per_day,'assignments':assignments,'optimal_gap':solver.BestObjectiveBound()-solver.ObjectiveValue()}
 return 'OPTIMAL',payload
