import {replay} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,notify,date,fmt} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {EntryType, JobStatus} from '../constants/enums.js';

const canWrite=()=>session.user.role!=='viewer';
const dt=v=>v==null?'—':date(v);
const typeBadge=t=>'<span class="badge '+esc(t)+'">'+esc(EntryType[t]||t)+'</span>';

function voucherTable(rows){
 if(!rows.length)return '<div class="empty">尚无凭证。先登记领用，或由演示种子生成台账。</div>';
 return '<div class="table-wrap"><table><thead><tr><th>凭证</th><th>备件</th><th>领用发生日</th><th>入账时间</th><th>版本</th><th>类型</th><th>件数</th><th>更正自</th></tr></thead><tbody>'
  +rows.map(v=>'<tr><td>#'+v.id+'</td><td>'+esc(v.part)+'</td><td>'+esc(v.occur_date)+'</td><td>'+esc(dt(v.posted_at))+'</td><td>v'+v.version+'</td><td>'+typeBadge(v.entry_type)+'</td><td>'+esc(fmt(v.demand))+'</td><td>'+(v.corrected_id?('#'+v.corrected_id):'—')+'</td></tr>').join('')
  +'</tbody></table></div>';
}

function compareBlock(c){
 if(!c)return '<div class="empty">选择会议时点后生成对照。只采信会议前已入账版本，差异逐条归因到凭证。</div>';
 const m=c.meeting,cur=c.current;
 const rate=(x)=>x.computable?esc(fmt(x.rate))+' <small>件/天</small>':'<span class="na">无法计算</span>';
 return '<div class="compare-grid">'
  +'<div class="metric-card"><p class="eyebrow">会议当时可得</p><strong>'+rate(m)+'</strong><span>可见凭证 '+m.visible_count+' 张'+(m.computable?(' · 末日 '+esc(m.last_occur_date)):'')+'</span>'+(m.computable?'':'<em>'+esc(m.reason)+'</em>')+'</div>'
  +'<div class="metric-card"><p class="eyebrow">现有凭证回算</p><strong>'+rate(cur)+'</strong><span>有效凭证 '+cur.visible_voucher_ids.length+' 张'+(cur.computable?(' · 末日 '+esc(cur.last_occur_date)):'')+'</span>'+(cur.computable?'':'<em>'+esc(cur.reason)+'</em>')+'</div>'
  +'<div class="metric-card delta"><p class="eyebrow">领用率差异</p><strong>'+(c.rate_delta==null?'—':esc(fmt(c.rate_delta)))+'</strong><span>差异凭证 '+c.diff_voucher_count+' 张</span></div>'
  +'</div>'
  +'<div class="section-title"><h2>差异来自哪些凭证</h2><span>'+c.diff_voucher_count+' 张</span></div>'
  +(c.diff_vouchers.length?'<div class="table-wrap"><table><thead><tr><th>凭证</th><th>发生日</th><th>入账时间</th><th>类型</th><th>件数</th><th>版本</th><th>滞后会议</th><th>归因</th></tr></thead><tbody>'
   +c.diff_vouchers.map(v=>'<tr><td>#'+v.id+'</td><td>'+esc(v.occur_date)+'</td><td>'+esc(dt(v.posted_at))+'</td><td>'+typeBadge(v.entry_type)+'</td><td>'+esc(fmt(v.demand))+'</td><td>v'+v.version+'</td><td>'+(v.lag_days?('+'+v.lag_days+' 天'):'同日')+'</td><td>'+esc(v.reason)+'</td></tr>').join('')
   +'</tbody></table></div>':'<p class="hint">两个口径凭证一致，无差异。</p>');
}

function runBlock(runs){
 if(!runs.length)return '<div class="empty">尚未发起回放。清单在运行开始时固化，中断后按原清单恢复，重试不重复归档。</div>';
 return runs.map(r=>'<article class="run-card"><header><strong>'+esc(r.name)+'</strong>'+('<span class="status '+esc(r.status)+'">'+esc(JobStatus[r.status]||r.status)+'</span>')+'</header>'
  +'<p class="hint">清单凭证 '+r.manifest.voucher_checksum_len+' 张 · 会议点 '+(r.manifest.meeting_epochs||[]).length+' 个 · 参数 '+esc(JSON.stringify(r.params))+'</p>'
  +(r.status==='failed'?('<p class="warning">'+esc(r.error)+'</p>'):'')
  +(r.result&&r.result.skipped_concurrent&&r.result.skipped_concurrent.length?('<p class="warning">运行期间并发补录 '+r.result.skipped_concurrent.length+' 张凭证未纳入本次清单，留待下次运行：'+esc(r.result.skipped_concurrent.map(x=>'#'+x.id).join('、'))+'</p>'):'')
  +'<button class="text-button" data-run-detail="'+r.id+'">查看会议点归档 ↗</button><div id="run-detail-'+r.id+'"></div></article>').join('');
}

function pointDetail(p){
 const f=p.payload.forecast,bt=p.payload.backtest,cmp=p.payload.comparison;
 const rows=(bt.points||[]).map(x=>({
  day:x.day, actual:fmt(x.actual), reference:fmt(x.reference_actual),
  predicted:x.computable?fmt(x.predicted):'无法计算',
  baseline:x.computable?fmt(x.baseline):'—', note:x.computable?'':x.reason,
 }));
 return '<div class="point-detail"><p class="eyebrow">会议 '+esc(date(p.meeting_epoch))+'</p>'
  +(f.computable?'<p>会议口径领用率 <strong>'+esc(fmt(f.rate))+'</strong> 件/天 · 14 天累计 '+esc(fmt(f.cumulative))+' 件（可见凭证 '+f.visible_count+' 张）</p>'
   :'<p class="warning">会议时点无法计算：'+esc(f.reason)+'</p>')
  +'<p class="hint">逐日回测 '+bt.computable_points+'/'+bt.total_points+' 个点具备 45 日历史依据；MAE='+(bt.mae==null?'无法计算':esc(fmt(bt.mae)))+'，均值基线 MAE='+(bt.baseline_mae==null?'无法计算':esc(fmt(bt.baseline_mae)))+'</p>'
  +'<div class="table-wrap"><table><thead><tr><th>回测日</th><th>实际(会议可见)</th><th>回算参考(现有凭证)</th><th>当时预测</th><th>均值基线</th><th>说明</th></tr></thead><tbody>'
  +rows.map(x=>'<tr><td>'+esc(x.day)+'</td><td>'+esc(x.actual)+'</td><td>'+esc(x.reference)+'</td><td>'+esc(x.predicted)+'</td><td>'+esc(x.baseline)+'</td><td>'+esc(x.note)+'</td></tr>').join('')
  +'</tbody></table></div>'
  +'<p class="hint">对照差异凭证 '+cmp.diff_voucher_count+' 张；归档 '+esc(p.archive_path||'')+'（幂等键 '+esc(p.idempotency_key)+'）</p></div>';
}

export async function replayPage(){
 const [vouchers,runs]=await Promise.all([replay.vouchers(),replay.runs()]);
 const m=workspace.meta;
 const partOptions=m.parameters.find(p=>p.key==='part').options;
 return heading('05 / POINT-IN-TIME','凭证台账与时点回放','登记领用发生日、入账时间与更正版本；按会议时点重放预测，逐日回测只取当时已入账版本。缺少历史依据的日期标明无法计算。')
 +'<div class="replay-grid">'
 +'<section class="panel"><div class="section-title"><h2>领用凭证台账</h2><span>'+vouchers.length+' 张</span></div>'
 +'<form id="voucher-filter"><label>备件筛选<select name="part"><option value="">全部</option>'+partOptions.map(o=>'<option value="'+esc(o.value)+'">'+esc(o.label)+'</option>').join('')+'</select></label></form>'
 +'<div id="voucher-list">'+voucherTable(vouchers)+'</div></section>'
 +'<section class="panel"><div class="section-title"><h2>登记凭证 / 补记 / 更正</h2></div>'
 +'<form id="voucher-form"><div class="parameter-grid"><label>备件<select name="part">'+partOptions.map(o=>'<option value="'+esc(o.value)+'">'+esc(o.label)+'</option>').join('')+'</select></label>'
 +'<label>领用发生日<input name="occur_date" type="date" required></label>'
 +'<label>入账时间<input name="posted_at" type="datetime-local" step="1" required></label>'
 +'<label>领用件数<input name="demand" type="number" step="any" min="0" value="1" required></label>'
 +'<label>类型<select name="entry_type"><option value="normal">常规领用</option><option value="backfill">领用补记（晚入账）</option><option value="correction">更正版本</option></select></label>'
 +'<label>版本<input name="version" type="number" min="1" value="1" required></label>'
 +'<label>更正凭证 id（可空）<input name="corrected_id" type="number" min="1" placeholder="仅更正时填写"></label></div>'
 +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>登记凭证 ↗</button>'
 +'<p class="hint">更正必须指向同备件、同发生日的原凭证；入账时间晚于会议的补记不会进入该会议预测。</p></form></section>'
 +'</div>'
 +'<section class="panel method-sheet"><p class="eyebrow">WORKBENCH / 会议时点对照</p><h2>会议当时可得预测 vs 现有凭证回算</h2>'
 +'<form id="compare-form" class="parameter-grid"><label>会议时点<input name="meeting_epoch" type="datetime-local" step="1" required value="2026-08-21T10:00"></label>'
 +m.parameters.map(p=>'<label>'+esc(p.label)+(p.type==='select'?'<select name="param-'+esc(p.key)+'">'+p.options.map(o=>'<option value="'+esc(o.value)+'" '+(o.value==p.default?'selected':'')+'>'+esc(o.label)+'</option>').join('')+'</select>':'<input type="number" step="any" name="param-'+esc(p.key)+'" value="'+esc(p.default)+'" min="'+esc(p.min??'')+'" max="'+esc(p.max??'')+'" required>')+'</label>').join('')
 +'<button class="primary" type="submit">生成对照 ↗</button></form><div id="compare-result"></div></section>'
 +'<section class="panel"><div class="section-title"><h2>发起会议时点回放</h2><span>清单固化 · 中断恢复 · 幂等归档</span></div>'
 +'<form id="run-form"><label>回放名称<input name="name" required minlength="2" value="周五例会回放 · 补记隔离"></label>'
 +'<label>会议时点（每行一个，YYYY-MM-DDTHH:MM）<textarea name="meeting_epochs" rows="4" required>2026-05-08T10:00&#10;2026-05-20T10:00&#10;2026-06-12T10:00&#10;2026-07-17T10:00&#10;2026-08-21T10:00&#10;2026-09-18T10:00</textarea></label>'
 +'<div class="parameter-grid">'+m.parameters.map(p=>'<label>'+esc(p.label)+(p.type==='select'?'<select name="param-'+esc(p.key)+'">'+p.options.map(o=>'<option value="'+esc(o.value)+'" '+(o.value==p.default?'selected':'')+'>'+esc(o.label)+'</option>').join('')+'</select>':'<input type="number" step="any" name="param-'+esc(p.key)+'" value="'+esc(p.default)+'" min="'+esc(p.min??'')+'" max="'+esc(p.max??'')+'" required>')+'</label>').join('')+'</div>'
 +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>固化清单并回放 ↗</button>'
 +'<p class="hint">运行开始后并发补录的凭证不进入本次清单；中断重启沿用原清单，已归档会议点自动跳过。</p></form>'
 +'<div id="run-list" class="run-list">'+runBlock(runs)+'</div></section>';
}

function collectParams(form){
 const params={};
 for(const p of workspace.meta.parameters){
  const value=form.get('param-'+p.key);
  params[p.key]=p.type==='number'?Number(value):value;
 }
 return params;
}

export function bindReplay(refresh){
 const filterSelect=document.querySelector('#voucher-filter select');
 filterSelect?.addEventListener('change',async e=>{
  const rows=await replay.vouchers(e.target.value||undefined);
  document.querySelector('#voucher-list').innerHTML=voucherTable(rows);
 });
 const vf=document.querySelector('#voucher-form');
 if(vf)submit(vf,async f=>{
  const corrected=f.get('corrected_id');
  const payload={part:f.get('part'),occur_date:f.get('occur_date'),posted_at:f.get('posted_at'),
   demand:Number(f.get('demand')),version:Number(f.get('version')),entry_type:f.get('entry_type'),
   corrected_id:corrected?Number(corrected):null};
  await replay.register([payload]);notify('凭证已登记，入账时间与版本已留痕');await refresh();
 });
 const cf=document.querySelector('#compare-form');
 if(cf)submit(cf,async f=>{
  const c=await replay.compare({meeting_epoch:f.get('meeting_epoch'),params:collectParams(f)});
  document.querySelector('#compare-result').innerHTML=compareBlock(c);
 });
 const rf=document.querySelector('#run-form');
 if(rf)submit(rf,async f=>{
  const meeting_epochs=f.get('meeting_epochs').split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  const run=await replay.createRun({name:f.get('name'),meeting_epochs,params:collectParams(f)});
  notify('回放已提交，运行 #'+run.id+' 按固化清单执行');await refresh();
 });
 document.querySelectorAll('[data-run-detail]').forEach(btn=>btn.onclick=async()=>{
  const box=document.querySelector('#run-detail-'+btn.dataset.runDetail);
  if(box.dataset.open){box.innerHTML='';box.dataset.open='';return;}
  const detail=await replay.run(btn.dataset.runDetail);
  box.innerHTML=(detail.points||[]).map(pointDetail).join('')||'<p class="hint">尚无完成的会议点，刷新后重试。</p>';
  box.dataset.open='1';
 });
}
