'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const display = (v, suffix='') => v == null ? '—' : `${v}${suffix}`;
const local = value => value ? new Date(value).toLocaleString() : '—';
const clock = value => value ? new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
let data, csrf, editing, afterLogin, timeout, generation = 0, chartSignature, authEnabled;
const states = new Map();
async function api(url, method='GET', body={}) {
  const options = method === 'GET' ? {} : {method, headers:{'Content-Type':'application/json','X-CSRF-Token':csrf || ''},body:JSON.stringify(body)};
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function toast(text, error=false) {
  $('toast').textContent=text;
  $('toast').className=`visible ${error ? 'error' : ''}`;
  clearTimeout(timeout);
  timeout=setTimeout(()=>$('toast').className='',5500);
}
document.addEventListener('pointerdown', e => {
  const b = e.target.closest('button.button, a.button');
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect();
  const size = Math.max(r.width, r.height) * 1.7;
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  ripple.style.width = ripple.style.height = `${size}px`;
  ripple.style.left = `${e.clientX - r.left - size / 2}px`;
  ripple.style.top = `${e.clientY - r.top - size / 2}px`;
  b.appendChild(ripple);
  setTimeout(() => ripple.remove(), 480);
});
const state = ep => ep.latest_ok == null ? 'pending' : ep.latest_ok ? 'up' : 'down';
const selected = new Set();
let visible = [], page = 1, sortKey = '', sortDir = 1, inspecting, muteUntil = Number(localStorage.getItem('sentinelMute') || 0);
function selection() {
  $('selection').hidden = !selected.size;
  $('selection').textContent = `${selected.size} selected`;
  const n=visible.filter(ep=>selected.has(ep.id)).length;
  $('select-all').checked=!!visible.length && n===visible.length;
  $('select-all').indeterminate=n>0 && n<visible.length;
}
function table() {
  if (!data) return;
  const q=$('search').value.trim().toLowerCase(), f=$('filter').value;
  const endpoints=data.endpoints.filter(ep => `${ep.name} ${ep.url} ${ep.description} ${state(ep)==='up'?'online':state(ep)==='down'?'failed':'pending'} ${ep.slow?'slow':''}`.toLowerCase().includes(q) && (f==='all' || f===state(ep) || f==='slow' && ep.slow));
  if(sortKey) endpoints.sort((a,b)=>a[sortKey]==null?1:b[sortKey]==null?-1:sortDir*(sortKey==='name'?a.name.localeCompare(b.name):a[sortKey]-b[sortKey]));
  const size=Number($('page-size').value), max=Math.max(1,Math.ceil(endpoints.length/size));
  page=Math.min(page,max);
  visible=endpoints.slice((page-1)*size,page*size);
  $('count').textContent=`${data.endpoints.length} configured`;
  $('page-info').textContent=endpoints.length?`Showing ${(page-1)*size+1}–${Math.min(page*size,endpoints.length)} of ${endpoints.length} endpoints`:'Showing 0 endpoints';
  $('page-number').textContent=`${page} / ${max}`;
  $('previous').disabled=page===1;
  $('next').disabled=page===max;
  $('endpoints').innerHTML=visible.map(ep=>{
    const s=state(ep), health=s==='up'?(ep.slow?'Slow':ep.latest_status?`HTTP ${ep.latest_status}`:'Online'):s==='down'?(ep.latest_status?`Failed · ${ep.latest_status}`:'Failed'):'Pending';
    return `<tr data-row="${ep.id}" class="${inspecting===ep.id?'highlight':''}"><td><input type="checkbox" data-select="${ep.id}" aria-label="Select ${escape(ep.name)}" ${selected.has(ep.id)?'checked':''}></td><td><div class="route"><span class="pill method">GET</span><div><strong>${escape(ep.name)}</strong><small title="${escape(ep.url)}">${escape(ep.url)}</small></div></div></td><td><span class="health ${s==='up'?(ep.slow?'violet':'mint'):s==='down'?'red':'pending'}"><i></i>${health}</span></td><td class="numeric">${display(ep.average,' ms')}</td><td class="numeric">${display(ep.uptime,'%')}</td><td class="numeric">${ep.interval} s</td><td class="numeric" title="${escape(local(ep.latest_at))}">${escape(clock(ep.latest_at))}</td><td class="row-actions"><button class="button" data-id="${ep.id}" data-action="details">Details</button><button class="button" data-id="${ep.id}" data-action="check">Test</button></td></tr>`;
  }).join('') || `<tr><td colspan="8" class="table-empty">${data.endpoints.length?'No monitors match your filters.':'No monitors yet. Add a public endpoint to get started.'}</td></tr>`;
  selection();
}
function plot(id, traces, extra, empty) {
  const el=$(id);
  if (!traces.length) {
    if (el._fullLayout) Plotly.purge(el);
    el.innerHTML=`<div class="plot-empty"><strong>${empty}</strong><span>Only recorded successful checks appear here.</span></div>`;
    return;
  }
  if (!window.Plotly) throw new Error('Chart library could not be loaded.');
  if (!el._fullLayout) el.innerHTML='';
  const axis={gridcolor:'#e3ebf7',zeroline:false,automargin:true};
  Plotly.react(el,traces,{height:235,margin:{l:48,r:20,t:9,b:42},paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{color:'#53657f',size:10},hoverlabel:{bgcolor:'#202c3c',font:{color:'#edf3fb'}},legend:{orientation:'h',y:-.24},uirevision:$('hours').value,xaxis:axis,yaxis:{...axis,rangemode:'tozero'},...extra},{responsive:true,displaylogo:false,displayModeBar:false}).catch(()=>toast('Chart rendering failed.',true));
}
function render() {
  const d=data;
  $('nav-count').textContent=d.endpoints.length;
  $('online').textContent=d.endpoints.filter(e=>state(e)==='up'&&!e.slow).length;
  $('slow').textContent=d.endpoints.filter(e=>state(e)==='up'&&e.slow).length;
  $('failed').textContent=d.endpoints.filter(e=>state(e)==='down').length;
  $('nav-failed').textContent=`${$('failed').textContent} active`;
  $('average').textContent=`Avg. ${display(d.average,' ms')}`;
  $('uptime').textContent=display(d.uptime,'%');
  $('rail-uptime').textContent=d.uptime==null?'No recorded checks':`${d.uptime}% check-based uptime`;
  for(const id of selected) if(!d.endpoints.some(e=>e.id===id)) selected.delete(id);
  if(inspecting) { const ep=d.endpoints.find(e=>e.id===inspecting); if(ep) details(ep); else closeInspector(); }
  $('export').href=`/api/history.csv?hours=${$('hours').value}`;
  $('updated').textContent=`${d.checks} checks · ${d.good} passed · ${d.bad} failed · Updated ${new Date(d.end).toLocaleTimeString()}`;
  $('error-count').textContent=`${d.bad} failed ${d.bad===1?'check':'checks'}`;
  const names=new Map(d.endpoints.map(e=>[e.id,e.name]));
  $('logs').innerHTML=d.errors.map(e=>`<tr><td>${escape(names.get(e.endpoint_id))}</td><td>${escape(local(e.ts))}</td><td>${display(e.status)}</td><td>${escape(e.error)}</td><td>${escape(e.source)}</td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">No failures recorded in this period.</td></tr>';
  table();
  const comparison=d.endpoints.filter(ep=>ep.average!=null).sort((a,b)=>a.average-b.average);
  const signature=JSON.stringify([$('hours').value,d.traces,comparison.map(e=>[e.name,e.average,e.slow])]);
  if (chartSignature!==signature) {
    chartSignature=signature;
    const successful=d.traces.filter(t=>t.y.some(v=>v!=null));
    plot('trend',successful.map(t=>({type:'scatter',mode:'lines+markers',name:escape(t.name),x:t.x,y:t.y,line:{color:['#2862dd','#6650b6','#0a8368','#ad5a10','#a73977'][(t.id-1)%5],width:2},marker:{size:5},hovertemplate:'%{y:.1f} ms<extra>%{fullData.name}</extra>'})),{},'No latency samples yet');
    plot('compare',comparison.length?[{type:'bar',orientation:'h',x:comparison.map(e=>e.average),y:comparison.map(e=>String(e.id)),customdata:comparison.map(e=>escape(e.name)),marker:{color:comparison.map(e=>e.slow?'#ff8595':'#3976ed'),cornerradius:4},hovertemplate:'%{customdata}: %{x:.1f} ms<extra></extra>'}]:[],{showlegend:false,yaxis:{type:'category',tickmode:'array',tickvals:comparison.map(e=>String(e.id)),ticktext:comparison.map(e=>escape(e.name.slice(0,22))),automargin:true}},'No successful checks yet');
  }
}
async function refresh() {
  const current=++generation;
  try {
    const result=await api(`/api/dashboard?hours=${$('hours').value}`);
    if(current!==generation) return;
    data=result;
    render();
    for(const ep of data.endpoints) {
      const s=state(ep);
      if(Date.now()>=muteUntil && s==='down' && states.has(ep.id) && states.get(ep.id)!=='down') toast(`${ep.name} failed: ${ep.latest_error}`,true);
      states.set(ep.id,s);
    }
  } catch(err) {
    if(current===generation) {
      $('updated').textContent='Dashboard data could not be refreshed';
      toast(err.message,true);
    }
  }
}
function authorized(callback) {
  if(csrf) return callback();
  if(authEnabled===false) return toast('Editing is disabled. Start the server with MONITOR_TOKEN set.',true);
  afterLogin=callback;
  if(!$('login').open) $('login').showModal();
}
function editor(ep) {
  editing=ep?.id;
  $('edit-title').textContent=ep?'Edit monitor':'Add monitor';
  const form=$('endpoint-form');
  const values=ep || {name:'',url:'',description:'',interval:60,threshold:500,expected_status:''};
  ['name','url','description','interval','threshold','expected_status'].forEach(k=>form.elements[k].value=values[k] ?? '');
  $('editor').showModal();
}
function closeInspector() {
  inspecting=null;
  $('inspector').hidden=true;
  $('split').classList.remove('inspecting');
  table();
}
function details(ep) {
  inspecting=ep.id;
  $('inspector-title').textContent=ep.name;
  const entries=[['HTTP status',display(ep.latest_status)],['Latest result',ep.latest_error || (ep.latest_ok ? 'Passed HTTP policy' : 'Pending')],['Interval',`${ep.interval} s`],['Expected status',ep.expected_status || 'Any 2xx'],['Successful checks',ep.good],['Failed checks',ep.bad],['Check-based uptime',display(ep.uptime,'%')],['Estimated downtime',`${ep.downtime} s`]];
  const points=(data.traces.find(t=>t.id===ep.id)?.y || []).slice(-24);
  const good=points.filter(v=>v!=null);
  const max=Math.max(1,...good);
  const spark=good.length ? `<svg viewBox="0 0 240 44" role="img" aria-label="Recent successful response latency">${points.map((v,i)=>{
    const x=5+i*230/Math.max(1,points.length-1), y=39-v*32/max;
    if(v==null) return '';
    const next=points[i+1];
    return `<circle cx="${x}" cy="${y}" r="2" fill="#2563eb"/>${next!=null?`<line x1="${x}" y1="${y}" x2="${x+230/Math.max(1,points.length-1)}" y2="${39-next*32/max}" stroke="#2563eb" stroke-width="1.5"/>`:''}`;
  }).join('')}</svg>`:'<p>No successful samples yet.</p>';
  $('inspector-body').innerHTML=`<p>${escape(ep.url)}</p><p>${escape(ep.description)}</p><div class="spark"><strong>Latency trend · ${display(ep.average,' ms avg')}</strong>${spark}</div><dl>${entries.map(([label,value])=>`<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join('')}</dl><p>HTTP success does not guarantee application health.</p><div class="dialog-actions"><a class="button" href="/api/history.csv?hours=${$('hours').value}&endpoint=${ep.id}">Export</a><button id="detail-edit" class="button">Edit</button><button id="detail-delete" class="button danger">Delete</button></div>`;
  $('detail-edit').onclick=()=>authorized(()=>editor(ep));
  $('detail-delete').onclick=()=>authorized(async()=>{
    if(!await ask({title:'Delete monitor?',text:`${ep.name} and its recorded history will be permanently deleted.`,label:'Delete monitor'})) return;
    await action($('detail-delete'),async()=>{await api(`/api/endpoints/${ep.id}`,'DELETE');closeInspector();toast('Monitor deleted.');await refresh();});
  });
  $('inspector').hidden=false;
  $('split').classList.add('inspecting');
}
$('unlock').onclick=()=>csrf?toast('Operator editing is unlocked for this session.'):authorized(()=>toast('Operator editing unlocked.'));
$('new-monitor').onclick=$('add').onclick=()=>authorized(()=>editor());
$('hours').onchange=refresh;
$('search').oninput=$('filter').onchange=$('page-size').onchange=()=>{page=1;table();};
$('previous').onclick=()=>{page--;table();};
$('next').onclick=()=>{page++;table();};
$('close-inspector').onclick=closeInspector;
$('select-all').onchange=e=>{visible.forEach(ep=>e.target.checked?selected.add(ep.id):selected.delete(ep.id));table();};
$('endpoints').onchange=e=>{if(e.target.dataset.select) {const id=Number(e.target.dataset.select); e.target.checked?selected.add(id):selected.delete(id); selection();}};
document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{sortDir=sortKey===b.dataset.sort?-sortDir:1;sortKey=b.dataset.sort;document.querySelectorAll('[data-sort]').forEach(c=>{c.closest('th').removeAttribute('aria-sort');c.textContent=c.textContent.replace(/ [↕↑↓]$/,' '+(c===b?(sortDir===1?'↑':'↓'):'↕'));});b.closest('th').setAttribute('aria-sort',sortDir===1?'ascending':'descending');page=1;table();});
$('density').onclick=e=>{const b=e.currentTarget, roomy=$('split').classList.toggle('roomy');b.setAttribute('aria-pressed',String(!roomy));b.querySelector('span').textContent=`Density: ${roomy?'Comfortable':'Compact'}`;};
const focusSearch=()=>{$('search').focus();$('search').select();};
$('focus-search').onclick=focusSearch;
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&!document.querySelector('dialog[open]')){e.preventDefault();focusSearch();}if(e.key==='Escape'&&document.activeElement===$('search')){$('search').value='';$('search').oninput();}});
function muteLabel(){const muted=Date.now()<muteUntil;$('mute').setAttribute('aria-pressed',String(muted));$('mute').querySelector('span').textContent=muted?'Unmute alerts':'Mute (1h)';}
$('mute').onclick=()=>{muteUntil=Date.now()<muteUntil?0:Date.now()+3600000;localStorage.setItem('sentinelMute',String(muteUntil));muteLabel();toast(muteUntil?'Automatic alert toasts muted for one hour.':'Automatic alerts unmuted.');};
muteLabel();
$('run-all').onclick=()=>authorized(()=>action($('run-all'),async()=>{
  const targets=(data?.endpoints||[]).filter(ep=>!selected.size||selected.has(ep.id));
  if(!targets.length) return toast('No monitors to check.');
  const label=$('run-all').querySelector('span');
  if(!await ask({title:'Run health checks now?',text:`This sends a real HTTP GET probe to ${targets.length===1?'1 endpoint':`${targets.length} endpoints`} and records each result against your history.`,label:'Run checks',danger:false})) return;
  let passed=0, failed=0, skipped=0;
  try {
    for(const [i,ep] of targets.entries()){
      label.textContent=`Checking ${i+1} / ${targets.length}…`;
      try {const r=await api(`/api/endpoints/${ep.id}/check`,'POST');r.ok?passed++:failed++;} catch {skipped++;}
    }
    await refresh();
    toast(`${passed} passed · ${failed} failed · ${skipped} not recorded`,!!(failed||skipped));
  } finally {label.textContent='Run All Health Checks';}
}));
const router=()=>{
  const hash=location.hash || '#monitors';
  const nav=[...document.querySelectorAll('.rail nav a')];
  const active=nav.find(a=>a.getAttribute('href')===hash)||nav[0];
  nav.forEach(a=>a.classList.toggle('active',a===active));
  $('page-label').textContent=active.getAttribute('href')==='#monitors'?'Monitored APIs':active.getAttribute('href')==='#errors'?'Incident History':active.textContent.trim();
};
window.addEventListener('hashchange',router);
router();
document.querySelectorAll('dialog .close').forEach(btn=>btn.onclick=()=>btn.closest('dialog').close());
async function action(button, callback) {
  const originalText = button.dataset.action === 'check' ? button.textContent : null;
  if (originalText) button.textContent = 'Checking…';
  button.disabled=true;
  button.classList.add('is-loading');
  const modal=button.closest('dialog');
  modal?.querySelector('.dialog-feedback')?.remove();
  try { await callback(); } catch(err) {
    if(modal?.open) {
      const msg=document.createElement('p');
      msg.className='dialog-feedback'; msg.setAttribute('role','alert'); msg.textContent=err.message; modal.append(msg);
    } else toast(err.message,true);
  }
  finally {
    button.disabled=false;
    button.classList.remove('is-loading');
    if (originalText) button.textContent = originalText;
  }
}
$('login-form').onsubmit=e=>{
  e.preventDefault();
  const form=e.currentTarget;
  action(e.submitter,async()=>{
    const result=await api('/api/login','POST',{token:form.elements.token.value});
    csrf=result.csrf;
    form.reset();
    $('login').close();
    afterLogin?.();
  });
};
$('endpoint-form').onsubmit=e=>{
  e.preventDefault();
  const fields=Object.fromEntries(new FormData(e.currentTarget));
  fields.interval=Number(fields.interval);
  fields.threshold=Number(fields.threshold);
  fields.expected_status=fields.expected_status?Number(fields.expected_status):null;
  action(e.submitter,async()=>{
    if(editing && data.endpoints.some(ep=>ep.id===editing && (ep.url!==fields.url.trim() || ep.expected_status!==fields.expected_status)) && !await ask({title:'Reset recorded history?',text:'Changing the URL or expected status clears the existing results for this monitor.',label:'Save anyway'})) return;
    await api(editing?`/api/endpoints/${editing}`:'/api/endpoints',editing?'PATCH':'POST',fields);
    $('editor').close();
    toast(editing?'Monitor updated.':'Monitor added. The first check will run shortly.');
    await refresh();
  });
};
$('confirm-ok').onclick=()=>$('confirm').close('ok');
function ask({title='Confirm action',text='',label='Continue',danger=true}={}){
  return new Promise(resolve=>{
    const dialog=$('confirm'), ok=$('confirm-ok');
    $('confirm-title').textContent=title; $('confirm-text').textContent=text;
    ok.textContent=label; ok.className=`button ${danger?'danger':'primary'}`;
    dialog.returnValue='cancel';
    dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true});
    dialog.showModal();
  });
}
$('endpoints').onclick=e=>{
  const btn=e.target.closest('button[data-id]');
  if(!btn) return;
  const ep=data.endpoints.find(x=>x.id===Number(btn.dataset.id));
  if(!ep) return;
  if(btn.dataset.action==='details') {details(ep);table();}
  else authorized(()=>action(btn,async()=>{
    const result=await api(`/api/endpoints/${ep.id}/check`,'POST');
    toast(result.ok?`${ep.name}: HTTP ${result.status} in ${result.latency} ms`:`${ep.name}: ${result.error}`,!result.ok);
    await refresh();
  }));
};
api('/api/session').then(r=>{csrf=r.csrf;authEnabled=r.enabled;}).catch(err=>toast(err.message,true));
refresh();
setInterval(()=>{muteLabel();if(!document.hidden) refresh();},10000);
