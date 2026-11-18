import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const replay={
 vouchers:(part)=>api('/replay/vouchers'+(part?('?part='+encodeURIComponent(part)):''),
 register:vouchers=>api('/replay/vouchers',{method:'POST',body:JSON.stringify({vouchers})}),
 runs:()=>api('/replay/runs'),
 run:id=>api('/replay/runs/'+id),
 createRun:data=>api('/replay/runs',{method:'POST',body:JSON.stringify(data)}),
 compare:body=>api('/replay/compare',{method:'POST',body:JSON.stringify(body)}),
};
