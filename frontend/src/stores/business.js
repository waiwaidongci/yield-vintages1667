import {loadAll} from '../api/business.js';import {loadScheduling} from '../api/scheduling.js';
export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],lineShifts:[],schedBatches:[],schedOrders:[],historyYield:{}},selected:null,schedSelected:null};
export async function reload(){store.data={...(await loadAll()),...(await loadScheduling())}}
export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
