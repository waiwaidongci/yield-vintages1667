import {request} from './client.js';
export const loadScheduling=async()=>Object.fromEntries(await Promise.all([
 ['lineShifts',()=>request('/scheduling/line-shifts')],
 ['schedBatches',()=>request('/scheduling/batches')],
 ['schedOrders',()=>request('/scheduling/orders')],
 ['historyYield',()=>request('/scheduling/history-yield')],
].map(async([k,fn])=>[k,await fn()])));
export const saveLineShift=data=>request('/scheduling/line-shifts','POST',data);
export const adjustLineShift=(id,delta)=>request('/scheduling/line-shifts/'+id+'/adjust','POST',{delta});
export const saveSchedBatch=data=>request('/scheduling/batches','POST',{data});
export const submitSchedOrder=data=>request('/scheduling/orders','POST',data);
export const retrySchedOrder=id=>request('/scheduling/orders/'+id+'/retry','POST',{});
