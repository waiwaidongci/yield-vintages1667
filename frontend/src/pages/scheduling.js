import {heading} from '../components/common/header.js';import {table} from '../components/common/table.js';import {esc,fmt,time} from '../utils/format.js';import {schedStatus} from '../components/common/sched_status.js';
function shiftOptions(d){return d.lineShifts.map(s=>`<option value="${s.id}">${esc(s.name)}（编号 ${esc(s.code)}，余量 ${s.capacity}，v${s.version}）</option>`).join('')}
function orderDetail(o){
 if(!o)return '<p class="empty">选择左侧排产单查看批次安排、保底件数或退回原因。</p>';
 const r=o.result||{};
 const assignRows=(o.assignments||[]).map(a=>({批次:a.batch_key,线班:a.line_shift_code,数量:a.quantity,保底合格件数:a.est_good_floor}));
 const perDay=r.per_day_good?Object.entries(r.per_day_good).map(([day,good])=>({历史生产日:day,预计合格件数:good})):[];
 return `<section class="report"><div class="report-top"><h2>${esc(o.name)}</h2>${schedStatus(o.status)}</div>
 <p>尝试次数 ${o.attempts} · 创建 ${time(o.created)} · 创建人 ${esc(o.created_by||'')}</p>
 <p class="checksum">封存快照 SHA256 ${esc(o.checksum)}</p>
 ${o.reject_reason?`<p class="error reject-box">退回原因：${esc(o.reject_reason)}</p>`:''}
 ${o.error?`<p class="error">求解异常：${esc(o.error)}</p>`:''}
 ${r.floor_good_pieces!=null?`<div class="metrics"><div><small>保底合格件数（最差历史日）</small><strong>${fmt(r.floor_good_pieces)}</strong></div><div><small>最差历史生产日</small><strong>${esc(r.worst_day)}</strong></div><div><small>求解耗时（秒）</small><strong>${fmt(r.elapsed_seconds)}</strong></div><div><small>参评历史日</small><strong>${(r.days_evaluated||[]).length}</strong></div></div>`:''}
 <h3>批次安排（每批不可拆，不超班余量）</h3>${assignRows.length?table(assignRows):'<p class="empty">该单尚未占用产能，没有批次安排。</p>'}
 ${perDay.length?`<details><summary>同一方案在 ${perDay.length} 个历史生产日的预计合格件数</summary>${table(perDay)}</details>`:''}
 ${o.status==='rejected'?`<button class="toggle" id="retry-order" data-id="${o.id}">按最新余量重试同一排产单（不重复占用产能）</button>`:''}
 <details><summary>接单封存内容（底稿口径与各线班班余量）</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px">${esc(JSON.stringify(o.snapshot,null,2))}</pre></details>
 </section>`;
}
export function scheduling(c,d){
 const selected=d.schedOrders.find(o=>o.id===d.schedSelected)||d.schedOrders[0];
 const shiftRows=d.lineShifts.map(s=>({编号:s.id,线班编码:s.code,名称:s.name,当前班余量:s.capacity,余量版本:s.version,状态:s.enabled?'启用':'停用'}));
 const batchRows=d.schedBatches.map(b=>({批次编号:b.data.key,名称:b.data.name,数量:b.data.quantity,可加工线班:b.data.eligible_shift_ids.join('、')}));
 return heading('ROBUST SCHEDULING / 05','试制排产求解（最差历史日保底最大化）','接单即封存所选底稿、口径与各线班班余量；CP-SAT 在 30 秒内未证最优便不占产能。每批不可拆，只能分到可加工线班且不超余量；并发下余量已变则整单退回重排，方案与扣减同事务生效。')
 +`<details open><summary>线班主数据与班余量（编码＝试制线|班次，与历史底稿对齐）</summary>${table(shiftRows)}<form id="shift-form" class="inline-form"><label>线班编码<input name="code" placeholder="试制线D|早班" required></label><label>名称<input name="name" placeholder="试制线D早班" required></label><label>班余量<input name="capacity" type="number" min="0" value="1000" required></label><button>新增/更新线班</button></form><form id="adjust-form" class="inline-form"><label>调整线班编号<select name="id">${d.lineShifts.map(s=>`<option value="${s.id}">${esc(s.code)}（当前 ${s.capacity}）</option>`).join('')}</select></label><label>余量增减（可负）<input name="delta" type="number" value="-100" required></label><button>模拟并发余量变动</button></form></details>`
 +`<details open><summary>试制批次主数据（不可拆，声明可加工线班）</summary>${table(batchRows)}<form id="batch-form" class="inline-form"><label>批次编号<input name="key" placeholder="TP-2611-05" required></label><label>名称<input name="name" placeholder="支架试制批" required></label><label>数量<input name="quantity" type="number" min="1" value="500" required></label><label>可加工线班（多选）<select name="eligible" multiple size="3">${shiftOptions(d)}</select></label><button>保存批次</button></form></details>`
 +`<details open><summary>发起排产（求解期间照常接单，不占用任何产能）</summary><form id="sched-order-form" class="inline-form"><label>排产单名称<input name="name" value="十一月试制鲁棒排产" required></label><label>幂等键（重试同一单不重复占用）<input name="idempotency_key" value="sched-${Date.now()}" required></label><label>封存口径版本<select name="policy_id">${d.policies.map(p=>`<option value="${p.id}">${esc(p.version)}</option>`).join('')}</select></label><label>纳入批次（多选）<select name="batch_keys" multiple size="4">${d.schedBatches.map(b=>`<option value="${esc(b.data.key)}" selected>${esc(b.data.key)} · ${esc(b.data.name)}</option>`).join('')}</select></label><button>发起排产求解</button></form></details>`
 +`<div class="run-layout"><aside class="run-list">${d.schedOrders.length?d.schedOrders.map(o=>`<button class="select-sched" data-id="${o.id}">${esc(o.name)} ${schedStatus(o.status)}<span class="sub">尝试 ${o.attempts} · 保底 ${o.result?.floor_good_pieces??'—'}</span></button>`).join(''):'<p class="empty">尚无排产单。</p>'}</aside><div>${orderDetail(selected)}</div></div>`;
}
