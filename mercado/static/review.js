const body=document.getElementById('itemsBody');
const number=value=>Number(String(value||0).replace(',','.'))||0;
function bind(row){
  row.querySelector('.remove-row').addEventListener('click',()=>{row.remove();reconcile()});
  row.querySelectorAll('.calc').forEach(input=>input.addEventListener('input',event=>{
    const qty=number(row.querySelector('.qty').value),unit=number(row.querySelector('.unit-price').value);
    if(event.target.classList.contains('qty')||event.target.classList.contains('unit-price'))row.querySelector('.gross').value=(qty*unit).toFixed(2);
    if(!event.target.classList.contains('item-total'))row.querySelector('.item-total').value=Math.max(0,number(row.querySelector('.gross').value)-number(row.querySelector('.discount').value)).toFixed(2);
    reconcile();
  }));
}
function reconcile(){
  const rows=[...body.querySelectorAll('tr')];
  const gross=rows.reduce((s,r)=>s+number(r.querySelector('.gross').value),0);
  const discount=rows.reduce((s,r)=>s+number(r.querySelector('.discount').value),0);
  const total=rows.reduce((s,r)=>s+number(r.querySelector('.item-total').value),0);
  const declared=number(document.querySelector('[name="total_paid"]').value);
  const delta=total-declared;
  document.getElementById('reconciliation').innerHTML='<strong>Conferência:</strong> '+rows.length+' linhas · bruto R$ '+gross.toFixed(2).replace('.',',')+' · descontos R$ '+discount.toFixed(2).replace('.',',')+' · itens R$ '+total.toFixed(2).replace('.',',')+' · diferença para o cupom R$ '+delta.toFixed(2).replace('.',',');
}
[...body.querySelectorAll('tr')].forEach(bind);
document.getElementById('addRow').addEventListener('click',()=>{const row=document.getElementById('rowTemplate').content.firstElementChild.cloneNode(true);body.append(row);bind(row);reconcile()});
document.querySelectorAll('.receipt-total').forEach(input=>input.addEventListener('input',reconcile));
reconcile();

