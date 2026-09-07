(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const metrics = [['growth','Sales growth %'],['monthly_sold','Monthly sold'],['commission','Commission %'],['price','Price'],['bsr','BSR'],['total_videos','Total videos']];
  const columns = [['asin','ASIN'],['title','Product title'],['brand','Brand'],['category','Category'],['price','Price'],['commission','CC commission'],['monthly_sold','Monthly sold'],['monthly_sold_90','90-day avg sold'],['growth','Sales growth %'],['bsr','Current BSR'],['bsr90','90-day BSR trend'],['merchant_video','Merchant video'],['total_videos','Total videos'],['influencer_videos','Influencer videos'],['budget_remaining','Budget remaining'],['available_slots','Available slots'],['score','Opportunity score'],['status','Status'],['campaigns','Campaigns']];
  let rows = [], filtered = [], page = 0;
  function element(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
  function format(key, value) {
    if (value == null) return 'Unavailable';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value !== 'number') return value || 'Unavailable';
    if (['price','budget','budget_remaining'].includes(key)) return value.toLocaleString('en-US',{style:'currency',currency:'USD'});
    return value.toLocaleString('en-US',{maximumFractionDigits:1}) + (['growth','commission','bsr90'].includes(key) ? '%' : '');
  }
  const header = element('tr'); columns.forEach(([,name]) => header.append(element('th', name))); $('resultHead').append(header);
  [['score','Opportunity score'],...metrics,['category','Category']].forEach(([key,label]) => { const option = element('option',label); option.value=key; $('opportunitySort').append(option); });
  for (const [key,name] of metrics) {
    const set = element('fieldset'); set.append(element('legend',name));
    for (const bound of ['min','max']) { const label = element('label',bound === 'min' ? 'Minimum' : 'Maximum'); const input = element('input'); input.type='number'; input.step='any'; input.id=`${key}-${bound}`; input.placeholder='Any'; label.append(input); set.append(label); }
    $('numericFilters').append(set);
  }
  function qualifies(row) { return row.qualified && row.valid_until > Date.now()/1000; }
  function status(row) { return qualifies(row) ? 'Qualified' : (row.valid_until <= Date.now()/1000 ? 'Expired data — resume scan' : row.failed_filters.join(', ').replaceAll('_',' ')); }
  async function details(row) {
    $('campaignDetails').replaceChildren(element('p','Loading…')); $('campaignDialog').showModal();
    try {
      if (!/^[A-Z0-9]{10}$/.test(row.asin)) throw new Error('Invalid ASIN');
      const response = await fetch(`data/influencer/details/${row.asin}.json`,{cache:'no-store'});
      if (!response.ok) throw new Error('Campaign details unavailable');
      const payload = await response.json(); $('campaignDetails').replaceChildren();
      for (const c of payload.campaigns) {
        const article = element('article'); article.append(element('h3', c.name || c.campaign_id || 'Campaign'));
        const dl = element('dl');
        for (const [key,label] of [['campaign_id','Campaign ID'],['brand','Brand'],['commission','Commission'],['start','Start'],['end','End'],['budget','Budget'],['budget_remaining','Budget remaining'],['available_slots','Available slots'],['total_slots','Total slots'],['recommended','Recommended']]) { dl.append(element('dt',label),element('dd',format(key,c[key]))); }
        article.append(dl); $('campaignDetails').append(article);
      }
    } catch(error) { $('campaignDetails').replaceChildren(element('p',error.message)); }
  }
  function render() {
    const search = $('opportunitySearch').value.toLowerCase();
    filtered = rows.filter(row => ($('showExcluded').checked || qualifies(row)) && (!$('categoryFilter').value || row.category === $('categoryFilter').value)
      && [row.asin,row.title,row.brand].some(v => String(v || '').toLowerCase().includes(search))
      && metrics.every(([key]) => ['min','max'].every(bound => { const input=$(`${key}-${bound}`); return input.value === '' || (row[key] != null && (bound === 'min' ? row[key]>=Number(input.value) : row[key]<=Number(input.value))); })));
    const key=$('opportunitySort').value, direction=$('sortDirection').value === 'asc' ? 1 : -1;
    filtered.sort((a,b) => a[key] == null ? (b[key] == null ? a.asin.localeCompare(b.asin) : 1) : b[key] == null ? -1 : direction*(key==='category' ? a[key].localeCompare(b[key]) : a[key]-b[key]) || a.asin.localeCompare(b.asin));
    const pages=Math.max(1,Math.ceil(filtered.length/50)); page=Math.min(page,pages-1);
    $('resultBody').replaceChildren();
    for (const row of filtered.slice(page*50,page*50+50)) {
      const tr=element('tr');
      for (const [key] of columns) {
        const td=element('td',null, key==='title' ? 'title-cell' : key==='category' ? 'category-cell' : null);
        if (key==='asin') { const link=element('a',row.asin); link.href=`https://www.amazon.com/dp/${encodeURIComponent(row.asin)}`; link.target='_blank'; link.rel='noopener'; td.append(link); }
        else if (key==='campaigns') { const button=element('button',`${row.qualifying_campaign_count} campaign(s)`); button.type='button'; button.addEventListener('click',()=>details(row)); td.append(button); }
        else if (key==='status') { td.textContent=status(row); td.className=qualifies(row)?'qualified-badge':'excluded-badge'; }
        else td.textContent=format(key,row[key]);
        tr.append(td);
      }
      $('resultBody').append(tr);
    }
    $('qualifiedCount').textContent=rows.filter(qualifies).length.toLocaleString();
    $('resultCount').textContent=`${filtered.length.toLocaleString()} matching products · ${$('showExcluded').checked?'including diagnostic rows':'qualified products only'}`;
    $('emptyResults').hidden=filtered.length>0; $('pageLabel').textContent=`Page ${page+1} of ${pages}`;
    $('previousPage').disabled=page===0; $('nextPage').disabled=page===pages-1;
  }
  document.querySelector('.opportunity-controls').addEventListener('input',()=>{page=0;render();});
  $('previousPage').addEventListener('click',()=>{page--;render();}); $('nextPage').addEventListener('click',()=>{page++;render();});
  $('closeCampaigns').addEventListener('click',()=>$('campaignDialog').close());
  $('resetFilters').addEventListener('click',()=>{document.querySelectorAll('.opportunity-controls input').forEach(input=>{input.value='';input.checked=false;}); $('categoryFilter').value=''; $('opportunitySort').value='score'; $('sortDirection').value='desc';page=0;render();});
  $('exportCsv').addEventListener('click',()=>{
    const escape=value=>'"'+String(value ?? '').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
    const text=[columns.map(([,label])=>escape(label)).join(','),...filtered.map(row=>columns.map(([key])=>escape(key==='status'?status(row):key==='campaigns'?row.qualifying_campaign_count:row[key])).join(','))].join('\r\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'})); const link=element('a');link.href=url;link.download='influencer-opportunities.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  async function load() {
    try {
      const response=await fetch('data/influencer/status.json',{cache:'no-store'});
      if(response.status===404) { $('scanStatus').textContent='No scan yet. Upload campaigns, then start the 100-ASIN test workflow.'; render();return; }
      if(!response.ok) throw new Error('Could not load scan status');
      const data=await response.json();
      for(const path of data.pages || []) {
        if(!/^page-\d{4}\.json$/.test(path) || rows.length>=10000) throw new Error('Invalid or oversized result index');
        const response=await fetch(`data/influencer/${path}`,{cache:'no-store'});
        if(!response.ok) throw new Error('Results are updating; reload shortly.');
        rows.push(...await response.json());
      }
      for(const category of [...new Set(rows.map(r=>r.category).filter(Boolean))].sort()) { const option=element('option',category);option.value=category;$('categoryFilter').append(option); }
      $('evaluatedCount').textContent=`${data.counts?.evaluated ?? 0} / ${data.selected ?? 0}`;
      $('tokenCount').textContent=`${data.tokens_consumed ?? '—'} / ${data.tokens_left ?? '—'}`;
      $('unavailableCount').textContent=data.counts?.unavailable_sales_trend ?? '—';
      $('scanStatus').textContent=`${data.phase.replaceAll('_',' ')} · Updated ${new Date(data.updated_at).toLocaleString()} · ${data.failed ?? 0} failed ASINs · ${data.tokens_reserved ?? 0} / ${data.token_budget ?? '—'} tokens reserved.${data.truncated?' Export capped at 10,000 rows. Full records are in the checkpoint.':''}`;
      render();
    } catch(error) { rows=[];render();$('scanStatus').textContent=error.message; }
  }
  load();
})();
