const $ = id => document.getElementById(id);
const BRL = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
const NUM = new Intl.NumberFormat('pt-BR',{maximumFractionDigits:2});
const COLORS=['#174f3b','#e77d42','#7ea45a','#c9a13a','#397b8c','#9b6f9e','#b94b43','#89a3a0','#6e8250'];
const state={rows:[],charts:{}};
const sum=(a,f)=>a.reduce((t,x)=>t+Number(f(x)||0),0);
const group=(a,f)=>a.reduce((m,x)=>{const k=f(x);(m[k]??=[]).push(x);return m},{});
const unique=(a,f)=>[...new Set(a.map(f))];
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function makeChart(id,type,data,options={}){
  if(typeof Chart==='undefined')return;
  state.charts[id]?.destroy();
  state.charts[id]=new Chart($(id),{type,data,options:{
    responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
    plugins:{legend:{labels:{usePointStyle:true,boxWidth:8,font:{size:10},color:'#68746d'}},tooltip:{backgroundColor:'#18231d',padding:10}},
    scales:{x:{grid:{display:false},ticks:{font:{size:9},color:'#68746d'}},y:{grid:{color:'#e7ebe3'},ticks:{font:{size:9},color:'#68746d'}}},
    ...options
  }});
}

async function load(){
  const params=new URLSearchParams();
  ['start','end','store','category','q'].forEach(id=>{if($(id).value)params.set(id,$(id).value)});
  const response=await fetch('/api/analytics?'+params.toString());
  const body=await response.json();
  state.rows=body.rows.filter(row=>!$('period').value||row.period===$('period').value);
  render();
}

function render(){
  const rows=state.rows,spend=sum(rows,x=>x.item_total),receipts=unique(rows,x=>x.receipt_id).length;
  $('spend').textContent=BRL.format(spend);$('spendNote').textContent=rows.length+' linhas de itens';
  $('trips').textContent=NUM.format(receipts);$('ticket').textContent=BRL.format(receipts?spend/receipts:0);
  $('products').textContent=NUM.format(unique(rows,x=>x.item_code||x.description).length);
  $('discount').textContent=BRL.format(sum(rows,x=>x.discount));$('rowCount').textContent=rows.length+' registros no recorte';
  renderDaily();renderCategory();renderStores();renderPeriods();renderFocus();renderPrice();renderInsights();renderComparison();renderVariation();renderDetails();
}

function renderDaily(){
  const g=group(state.rows,x=>x.date),labels=Object.keys(g).sort();
  makeChart('dailyChart','bar',{labels:labels.map(x=>x.split('-').reverse().join('/')),datasets:[
    {label:'Gasto',data:labels.map(k=>sum(g[k],x=>x.item_total)),backgroundColor:'#174f3b',borderRadius:7,yAxisID:'y'},
    {label:'Linhas',type:'line',data:labels.map(k=>g[k].length),borderColor:'#e77d42',backgroundColor:'#e77d42',pointRadius:4,yAxisID:'y1'}
  ]},{scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:v=>BRL.format(v)}},y1:{beginAtZero:true,position:'right',grid:{display:false},ticks:{precision:0}}}});
}
function renderCategory(){
  const entries=Object.entries(group(state.rows,x=>x.category)).map(([k,v])=>[k,sum(v,x=>x.item_total)]).sort((a,b)=>b[1]-a[1]);
  makeChart('categoryChart','doughnut',{labels:entries.map(x=>x[0]),datasets:[{data:entries.map(x=>x[1]),backgroundColor:COLORS,borderColor:'#fffef9',borderWidth:3}]},{cutout:'62%',plugins:{legend:{position:'right',labels:{usePointStyle:true,boxWidth:8,font:{size:9}}},tooltip:{callbacks:{label:c=>' '+c.label+': '+BRL.format(c.parsed)}}},scales:{x:{display:false},y:{display:false}}});
}
function receiptTotals(rows){
  return Object.values(group(rows,x=>x.receipt_id)).map(v=>({store:v[0].merchant_name+' · '+v[0].merchant_cnpj,period:v[0].period,total:sum(v,x=>x.item_total)}));
}
function renderStores(){
  const g=group(state.rows,x=>x.merchant_name+' · '+x.merchant_cnpj);
  const entries=Object.entries(g).map(([k,v])=>{const r=receiptTotals(v);return[k,sum(v,x=>x.item_total),sum(r,x=>x.total)/(r.length||1)]}).sort((a,b)=>b[1]-a[1]);
  makeChart('storeChart','bar',{labels:entries.map(x=>x[0]),datasets:[{label:'Gasto',data:entries.map(x=>x[1]),backgroundColor:'#174f3b',borderRadius:6},{label:'Ticket',data:entries.map(x=>x[2]),backgroundColor:'#cfe66d',borderRadius:6}]},{indexAxis:'y',scales:{x:{beginAtZero:true,ticks:{callback:v=>BRL.format(v)}},y:{grid:{display:false}}}});
}
function renderPeriods(){
  const labels=['Início (1–10)','Meio (11–20)','Fim (21–31)'],g=group(state.rows,x=>x.period),rg=group(receiptTotals(state.rows),x=>x.period);
  makeChart('periodChart','bar',{labels,datasets:[{label:'Gasto',data:labels.map(k=>sum(g[k]||[],x=>x.item_total)),backgroundColor:'#287458',borderRadius:6},{label:'Ticket',data:labels.map(k=>{const r=rg[k]||[];return r.length?sum(r,x=>x.total)/r.length:0}),backgroundColor:'#e77d42',borderRadius:6}]},{scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:v=>BRL.format(v)}}}});
}
function renderFocus(){
  const current=$('focusProduct').value;
  const products=Object.entries(group(state.rows,x=>x.item_code||x.description)).map(([k,v])=>({key:k,name:v[0].description,count:unique(v,x=>x.date).length})).sort((a,b)=>b.count-a.count||a.name.localeCompare(b.name));
  $('focusProduct').innerHTML=products.map(x=>'<option value="'+esc(x.key)+'">'+esc(x.name)+' · '+x.count+' data(s)</option>').join('');
  if(products.some(x=>x.key===current))$('focusProduct').value=current;
}
function renderPrice(){
  const rows=state.rows.filter(x=>(x.item_code||x.description)===$('focusProduct').value).sort((a,b)=>a.purchased_at.localeCompare(b.purchased_at));
  makeChart('priceChart','line',{labels:rows.map(x=>x.date_br+' · '+x.merchant_name),datasets:[{label:'Etiqueta',data:rows.map(x=>x.unit_price),borderColor:'#97a39b',backgroundColor:'#97a39b',pointRadius:5},{label:'Efetivo',data:rows.map(x=>x.effective_price),borderColor:'#e77d42',backgroundColor:'#e77d42',pointRadius:5}]},{scales:{x:{grid:{display:false}},y:{beginAtZero:false,ticks:{callback:v=>BRL.format(v)}}}});
}
function renderInsights(){
  if(!state.rows.length){$('insights').innerHTML='<div class="empty">Sem dados.</div>';return}
  const spend=sum(state.rows,x=>x.item_total);
  const stores=Object.entries(group(state.rows,x=>x.merchant_name)).map(([k,v])=>[k,sum(v,x=>x.item_total)]).sort((a,b)=>b[1]-a[1]);
  const categories=Object.entries(group(state.rows,x=>x.category)).map(([k,v])=>[k,sum(v,x=>x.item_total)]).sort((a,b)=>b[1]-a[1]);
  const products=Object.values(group(state.rows,x=>x.item_code||x.description)).map(v=>{const ds=unique(v,x=>x.date).length,lo=Math.min(...v.map(x=>x.effective_price)),hi=Math.max(...v.map(x=>x.effective_price));return{name:v[0].description,dates:ds,lo,hi,variation:lo?100*(hi/lo-1):0}}).filter(x=>x.dates>1).sort((a,b)=>b.variation-a.variation);
  const notes=[[stores[0][0]+' concentra '+(100*stores[0][1]/spend).toFixed(1).replace('.',',')+'%',BRL.format(stores[0][1])+' no recorte.'],[categories[0][0]+' lidera o gasto',(100*categories[0][1]/spend).toFixed(1).replace('.',',')+'% do total filtrado.']];
  if(products[0])notes.push([products[0].name+': '+products[0].variation.toFixed(1).replace('.',',')+'% de variação',BRL.format(products[0].lo)+' a '+BRL.format(products[0].hi)+' no preço efetivo.']);
  notes.push([BRL.format(sum(state.rows,x=>x.discount))+' em descontos','Diferença registrada entre valor bruto e líquido.']);
  $('insights').innerHTML=notes.map(x=>'<div class="note" style="margin-bottom:9px"><strong>'+esc(x[0])+'</strong><br>'+esc(x[1])+'</div>').join('');
}
function renderComparison(){
  const out=[];
  for(const v of Object.values(group(state.rows,x=>x.item_code||x.description))){
    const sg=group(v,x=>x.merchant_name+' · '+x.merchant_cnpj);if(Object.keys(sg).length<2)continue;
    const av=Object.entries(sg).map(([k,a])=>[k,sum(a,x=>x.effective_price)/a.length]).sort((a,b)=>a[1]-b[1]);
    out.push({name:v[0].description,best:av[0][0],low:av[0][1],high:av.at(-1)[1],spread:100*(av.at(-1)[1]/av[0][1]-1),stores:av.length});
  }
  out.sort((a,b)=>b.spread-a.spread);
  $('storeComparison').innerHTML=out.length?makeTable(['Produto','Mais barato em','Menor médio','Maior médio','Diferença','Lojas'],out.map(x=>[x.name,x.best,BRL.format(x.low),BRL.format(x.high),x.spread.toFixed(1).replace('.',',')+'%',x.stores])):'<div class="empty">Ainda não há produtos comparáveis entre lojas.</div>';
}
function renderVariation(){
  const out=Object.values(group(state.rows,x=>x.item_code||x.description)).map(v=>{const dates=unique(v,x=>x.date).length,lo=Math.min(...v.map(x=>x.effective_price)),hi=Math.max(...v.map(x=>x.effective_price));return{name:v[0].description,dates,stores:unique(v,x=>x.merchant_cnpj).length,lo,hi,spread:lo?100*(hi/lo-1):0,spend:sum(v,x=>x.item_total)}}).filter(x=>x.dates>1).sort((a,b)=>b.spread-a.spread);
  $('variation').innerHTML=out.length?makeTable(['Produto','Datas','Lojas','Mínimo','Máximo','Variação','Gasto'],out.map(x=>[x.name,x.dates,x.stores,BRL.format(x.lo),BRL.format(x.hi),x.spread.toFixed(1).replace('.',',')+'%',BRL.format(x.spend)])):'<div class="empty">São necessárias compras do mesmo produto em datas diferentes.</div>';
}
function renderDetails(){
  const rows=[...state.rows].sort((a,b)=>b.purchased_at.localeCompare(a.purchased_at)).slice(0,200);
  $('details').innerHTML=rows.length?makeTable(['Data','Mercado','Produto','Categoria','Qtd.','Etiqueta','Efetivo','Total','Desconto'],rows.map(x=>[x.date_br,x.merchant_name,x.description,x.category,NUM.format(x.quantity)+' '+x.unit,BRL.format(x.unit_price),BRL.format(x.effective_price),BRL.format(x.item_total),BRL.format(x.discount)])):'<div class="empty">Nenhum item encontrado.</div>';
}
function makeTable(headers,rows){
  return '<div class="table-wrap"><table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map((x,i)=>'<td class="'+(i>=2?'num':'')+'">'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
['start','end','store','category','period'].forEach(id=>$(id).addEventListener('change',load));
$('q').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(load,250)});
$('focusProduct').addEventListener('change',renderPrice);
$('clear').addEventListener('click',()=>{['start','end','store','category','period','q'].forEach(id=>$(id).value='');load()});
load();
