import {SCHED_STATUSES} from '../../constants/enums.js';import {esc} from '../../utils/format.js';
export const schedStatus=s=>'<span class="status sched-'+esc(s)+'">'+(SCHED_STATUSES[s]||esc(s))+'</span>';
