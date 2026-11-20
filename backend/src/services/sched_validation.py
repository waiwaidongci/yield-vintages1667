def integer(value,name,minimum=1):
 if isinstance(value,bool) or not isinstance(value,int) or value<minimum: raise ValueError(name+'必须为不小于%s的整数'%(minimum,))
 return value
def text(value,name,lo=1,hi=120):
 if not isinstance(value,str) or not lo<=len(value.strip())<=hi: raise ValueError(name+'长度需在%s~%s之间'%(lo,hi))
 return value.strip()
def validate_batch(d):
 if not isinstance(d,dict): raise ValueError('批次数据必须为对象')
 required={'key','name','quantity','eligible_shift_ids'}
 if set(d)!=required: raise ValueError('批次字段必须恰好为 %s'%sorted(required))
 text(d['key'],'批次编号',1,60);text(d['name'],'批次名称',1,120)
 integer(d['quantity'],'批次数量',1)
 ids=d['eligible_shift_ids']
 if not isinstance(ids,list) or not ids or any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in ids): raise ValueError('可加工线班必须为非空整数编号列表')
 if len(set(ids))!=len(ids): raise ValueError('批次 %s 的可加工线班存在重复'%d['key'])
def validate_line_shift(d):
 if not isinstance(d,dict) or set(d)!={'code','name','capacity'}: raise ValueError('线班字段必须恰好为 code/name/capacity')
 text(d['code'],'线班编号',1,60);text(d['name'],'线班名称',1,120);integer(d['capacity'],'班余量',0)
