const $ = (id) => document.getElementById(id);
const published = Boolean(document.querySelector('meta[name="published-dashboard"]'));
const exportLink = id => published ? `./exports/${id}.json` : `/api/export?source=${id}`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits = 0) => value == null ? '—' : Number(value).toLocaleString('en-GB', {maximumFractionDigits:digits, minimumFractionDigits:digits});
const pct = value => value == null ? '—' : `${fmt(value, 2)}%`;
const clock = date => date ? new Date(date).toLocaleTimeString('en-GB', {timeZone:'Europe/Prague', hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
const stamp = date => date ? new Date(date).toLocaleString('en-GB', {timeZone:'Europe/Prague'}) : '—';
// Source timestamps have no UTC offset; display them verbatim as Czech local time.
const sourceStamp = date => date ? date.replace('T', ' ') : '—';
const colors = ['#84d9b5','#8db5fc','#f2b56e','#caa5fa','#f58fa4','#79d1dc','#d7d680','#baac98','#e98ee2','#a5bbdf','#e4b59f','#b3d69a','#efcefa','#e5dfc4','#9da6ff','#eab969'];
const color = id => colors[(Number(id)-1) % colors.length];
const noSeatColor = '#7e8794';
let data, range = 0;
const selectedRounds = {};
let connected = false;

function chart(history, series, title, maximum) {
  if (!history.length || !series.length) return '<div class="empty">Waiting for results to plot.</div>';
  const width=Math.max(850, Math.min(window.innerWidth-48, 1500)), height=Math.max(225, series.length*20+34), left=57, right=480, top=14, bottom=30;
  const values = series.flatMap(s => history.map(s.value)).filter(v => v != null);
  const max = maximum || Math.max(5, Math.ceil(Math.max(0,...values)/5)*5);
  const start = new Date(history[0].captured).getTime(), end = new Date(history.at(-1).captured).getTime();
  const x = h => left + (end === start ? 1 : (new Date(h.captured).getTime()-start)/(end-start))*(width-left-right);
  const y = v => height-bottom-(v/max)*(height-top-bottom);
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}"><title>${esc(title)}. Hover over points for captured values.</title>`;
  for (let i=0;i<=4;i++) {const v=max*i/4;svg+=`<line class="grid" x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}"/><text text-anchor="end" x="${left-9}" y="${y(v)+4}">${fmt(v, v%1 ? 1 : 0)}%</text>`;}
  for (const [index, s] of series.entries()) {
    let path='', previous=false;
    for (const h of history) {const value=s.value(h);if(value==null){previous=false;continue;}path+=`${previous?'L':'M'}${x(h)},${y(value)} `;previous=true;}
    svg+=`<g class="chart-series" data-line="${index}"><path class="series-line" d="${path}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
    for (const h of history) {const value=s.value(h);if(value==null)continue;svg+=`<circle cx="${x(h)}" cy="${y(value)}" r="${history.length<3?4:2.5}" fill="${s.color}"><title>${esc(s.name)}: ${pct(value)} · ${stamp(h.captured)}</title></circle>`;}
    svg+='</g>';
  }
  const labels = series.map((s,index) => {
    const last = history.findLast(h => s.value(h) != null);
    return last ? { ...s, index, last, value:s.value(last), target:y(s.value(last)) } : null;
  }).filter(Boolean).sort((a,b)=>a.target-b.target);
  labels.forEach((label,i)=>{label.position=Math.max(label.target, i ? labels[i-1].position+20 : top+6);});
  if(labels.length && labels.at(-1).position>height-bottom){
    labels.at(-1).position=height-bottom;
    for(let i=labels.length-2;i>=0;i--) labels[i].position=Math.min(labels[i].position,labels[i+1].position-20);
  }
  for(const label of labels){
    const name=label.name.length>33 ? label.name.slice(0,32)+'…' : label.name;
    svg+=`<g class="chart-label" data-line="${label.index}" tabindex="0" aria-label="${esc(label.name)}: ${pct(label.value)}"><path class="label-connector" d="M${x(label.last)},${label.target} L${width-right+12},${label.target} L${width-332},${label.position} L${width-324},${label.position}" fill="none" stroke="${label.color}" stroke-width="1" opacity=".65"/><text class="end-label" x="${width-318}" y="${label.position+5}" style="fill:${label.color}"><title>${esc(label.name)}: ${pct(label.value)}</title>${esc(name)}</text><text class="end-label" text-anchor="end" x="${width-4}" y="${label.position+5}" style="fill:${label.color}">${pct(label.value)}</text></g>`;
  }
  svg+=`<text x="${left}" y="${height-8}">${clock(history[0].captured)}</text><text text-anchor="end" x="${width-right}" y="${height-8}">${clock(history.at(-1).captured)}</text></svg>`;
  return `<div class="chart-scroll">${svg}</div>`;
}

function render() {
  if (!data) return;
  $('overview').innerHTML = data.sources.map(s => {
    const latest = s.history.at(-1), r = latest?.rounds.at(-1);
    return `<a href="#election-${s.id}">${esc(s.id==='senate'?'Senate':s.title)} <strong>${fmt(r?.counted)}/${fmt(r?.total)}</strong> <span>(${pct(r?.percent)})</span>${s.status.error?' · retrying':''}</a>`;
  }).join('');
  $('overview').insertAdjacentHTML('afterbegin','<span>Precincts counted:</span>');
  const openDetails = new Set([...document.querySelectorAll('details[open]')].map(d=>d.id));
  $('detail').innerHTML = ['jundrov', 'brno', 'senate'].map(id => {
    const source = data.sources.find(s => s.id === id);
    return source ? `<section class="election-section" id="election-${id}" aria-label="${esc(source.title)}">${renderSource(source)}</section>` : '';
  }).join('');
  for(const id of openDetails) {if($(id)) $(id).open=true;}
}

function renderSource(s) {
  const selected = s.id, latest=s.history.at(-1);
  if (!latest) {
    return `<h2>${esc(s.title)}</h2><p>${esc(s.subtitle)}</p><div class="empty">${s.status.error ? `Could not fetch results: ${esc(s.status.error)}. Automatic retry is enabled.` : 'Fetching the first official snapshot…'}</div>`;
  }
  let selectedRound = selectedRounds[selected] || 1;
  if (!latest.rounds.some(r=>r.round===selectedRound)) selectedRound=latest.rounds.at(-1).round;
  const roundOf = snapshot => snapshot?.rounds.find(r => r.round === selectedRound);
  const r=roundOf(latest), previous=roundOf(s.history.at(-2));
  const entries=[...r.entries].sort((a,b)=>(b.votes??0)-(a.votes??0));
  const cutoff=Date.now()-range*3600000;
  const history=s.history.filter(h=>(!range || new Date(h.captured).getTime()>=cutoff) && roundOf(h));
  const validHistory=history.filter(h=>roundOf(h).counted>0);
  const seatCounts=new Map((latest.mandates?.parties || []).map(p=>[p.id,p.maximum]));
  const partyColor=id=>seatCounts.get(id)===0 ? noSeatColor : color(id);
  const series=entries.map(e=>({name:e.name, color:partyColor(e.id), value:h=>roundOf(h).entries.find(v=>v.id===e.id)?.share??null}));
  const old = new Map(previous?.entries.map(e=>[e.id,e]) || []);
  const stale=s.status.success && Date.now()-new Date(s.status.success).getTime()>data.interval*2500;
  const rounds=s.kind==='senate' ? `<label>Round <select data-round="${s.id}">${latest.rounds.map(v=>`<option value="${v.round}" ${v.round===selectedRound?'selected':''}>${v.round}</option>`).join('')}</select></label>` : '';
  return `
    <div class="title-row"><h2>${esc(s.title)} <span class="section-context">${s.id==='jundrov'?'District council':s.id==='brno'?'City council':'Brno-město'}</span></h2><div class="links">${rounds}<span>Turnout ${pct(r.turnout)} · Precincts counted ${pct(r.percent)}</span><a href="${s.page}" target="_blank" rel="noreferrer">Source ↗</a></div></div>
    ${s.status.error||stale?`<div class="warning">${s.status.error?`Collection failed: ${esc(s.status.error)}. Retrying automatically.`:'Collector has not checked in recently.'} Showing the last saved results.</div>`:''}
    ${chart(validHistory,series,`${s.title}: vote share over time (%)`)}
    ${mandateSummary(latest.mandates)}
    <details id="details-${s.id}"><summary>Detailed results & counting history <span>${fmt(r.votes)} ${s.kind==='senate'?'valid ballots':'candidate votes'} · ${s.history.length} snapshots</span></summary>
    <section class="panel"><h3>Counting progress & turnout</h3>${chart(history,[{name:'Precincts counted',color:colors[0],value:h=>roundOf(h).percent},{name:'Turnout',color:colors[1],value:h=>roundOf(h).turnout}], 'Counting progress and turnout',100)}</section>
    <section class="panel results"><div class="section-heading"><h3>Current standings</h3><small>Δ since previous snapshot</small></div>${r.counted && entries.length?`<div class="table-scroll"><table><thead><tr><th>${s.kind==='senate'?'Candidate':'Party / list'}</th><th>Votes</th><th>Share</th><th>Δ pp</th></tr></thead><tbody>${entries.map(e=>{
      const before=old.get(e.id), delta=previous?.counted && before?.share!=null && e.share!=null ? e.share-before.share:null;
      return `<tr><td><div class="party" ${seatCounts.get(e.id)===0?`style="color:${noSeatColor}"`:''}><span class="dot" style="background:${partyColor(e.id)}"></span>${esc(e.name)}</div>${e.party?`<small>${esc(e.party)}</small>`:''}<div class="bar"><span style="width:${e.share||0}%;background:${partyColor(e.id)}"></span></div></td><td>${fmt(e.votes)}</td><td>${pct(e.share)}</td><td class="${delta>0?'up':delta<0?'down':''}">${delta==null?'—':`${delta>0?'+':''}${fmt(delta,2)}`}</td></tr>`;
    }).join('')}</tbody></table></div>`:'<div class="empty">No precincts reported yet.<br>Results will appear automatically.</div>'}
    <p class="note">${s.kind==='municipal'?'Municipal voters can vote for multiple candidates: candidate votes are not the number of voters.':'Vote shares are for the selected Senate round.'} Partial counts are not projections. “pp” means percentage points.</p></section>
    ${latest.mandates ? '<p class="note">Grey lines and names indicate zero seats in the current allocation. Provisional mandates apply the municipal D’Hondt method to votes counted so far, with the 5% qualification threshold adjusted for each list’s candidate count, statutory threshold reduction, and candidate-count caps. Equal quotients favour the list with more votes; an unresolved lottery is shown as a range. This is a current-count allocation, not a forecast of uncounted precincts. Published official seat counts replace the estimate when available. <a href="https://www.zakonyprolidi.cz/cs/2001-491#p45" target="_blank" rel="noreferrer">Allocation rules (§45) ↗</a></p>' : ''}
    <div class="note">Last successful check: ${stamp(s.status.success)} · Source generated: ${esc(sourceStamp(latest.generated))}<br>History started: ${stamp(s.history[0].captured)} · <a href="${exportLink(s.id)}">Export snapshots ↓</a></div></details>`;
}

function mandateSummary(m) {
  if(!m) return '';
  if(m.status==='waiting') return `<div class="mandates"><strong>Mandates · ${m.total} seats</strong><span>Waiting for the first counted precinct · majority ${m.majority}</span></div>`;
  if(m.status==='unavailable') return '<div class="mandates">Mandate estimate unavailable for multiple electoral districts.</div>';
  return `<div class="mandates" aria-label="Council mandates"><strong>${m.status==='official'?'Official':'Provisional'} mandates</strong>${[...m.parties].sort((a,b)=>b.maximum-a.maximum || b.votes-a.votes).map(p=>{const c=p.maximum===0?noSeatColor:color(p.id);return `<span class="mandate-party" ${p.maximum===0?`style="color:${c}"`:''}><i class="dot" style="background:${c}"></i>${esc(p.name)} <b style="color:${c}">${p.minimum===p.maximum?p.minimum:`${p.minimum}–${p.maximum}`}</b></span>`;}).join('')}<span class="mandate-total">${m.total} seats · majority ${m.majority}${m.tie?' · tied seat requires a draw':''}</span></div>`;
}

document.addEventListener('change',e=>{
  if(e.target.matches('[data-round]')){selectedRounds[e.target.dataset.round]=Number(e.target.value);render();}
});
function highlightLabel(event, active) {
  const label=event.target.closest?.('.chart-label');
  if(!label || (event.relatedTarget && label.contains(event.relatedTarget))) return;
  const svg=label.closest('svg');
  label.classList.toggle('highlighted',active);
  svg.querySelector(`.chart-series[data-line="${label.dataset.line}"]`)?.classList.toggle('highlighted',active);
}
document.addEventListener('mouseover',e=>highlightLabel(e,true));
document.addEventListener('mouseout',e=>highlightLabel(e,false));
document.addEventListener('focusin',e=>highlightLabel(e,true));
document.addEventListener('focusout',e=>highlightLabel(e,false));
$('range').addEventListener('change',e=>{range=Number(e.target.value);render();});
async function refresh(){
  try{
    const response=await fetch(published ? `./dashboard.json?t=${Date.now()}` : '/api/dashboard',{signal:AbortSignal.timeout(12000)});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    data=await response.json();connected=true;$('error').hidden=true;
    const failures=data.sources.filter(s=>s.status.error).length;
    const stale=published && Date.now()-new Date(data.publishedAt).getTime()>20*60000;
    $('connection').textContent=stale?'Update delayed':failures?`Live · ${failures} retrying`:published?'Auto-updating':'Live';
    render();
  }catch(error){connected=false;$('connection').textContent='Disconnected';$('error').hidden=false;$('error').textContent='Cannot load dashboard updates. Showing the last loaded data; retrying automatically.';}
  setTimeout(refresh,published?60000:10000);
}
setInterval(()=>{if(data)$('countdown').textContent=connected?(published?`Published ${stamp(data.publishedAt)} · scheduled about every 5 min`:`Collects every ${data.interval}s · next check in ${Math.max(0,Math.ceil(data.nextPoll-Date.now()/1000))}s`):'Waiting for connection…';},1000);
refresh();
