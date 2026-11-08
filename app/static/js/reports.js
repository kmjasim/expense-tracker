(() => {
  'use strict';
  const form = document.getElementById('report-form');
  if (!form) return;
  const list = document.getElementById('report-rules');
  const hidden = document.getElementById('report-rules-json');
  const fields = JSON.parse(document.getElementById('report-rule-fields').textContent);
  const numeric = new Set(['amount','absolute_amount','fee_amount','fx_rate','sent_krw','received_bdt']);
  const dates = new Set(['date','created_at','updated_at']);
  const labels = {eq:'equals',ne:'does not equal',contains:'contains',not_contains:'does not contain',gte:'at least / on or after',lte:'at most / on or before',gt:'greater than / after',lt:'less than / before',empty:'is empty',not_empty:'is not empty'};
  function option(value, text) {const node=document.createElement('option');node.value=value;node.textContent=text;return node;}
  function add(rule={field:'note',op:'contains',value:''}) {
    if (list.children.length >= 12) return;
    const row=document.createElement('div');row.className='rule-row';
    const field=document.createElement('select');field.className='form-select';field.setAttribute('aria-label','Condition field');
    fields.forEach(key=>field.append(option(key,key.replaceAll('_',' '))));field.value=rule.field;
    const op=document.createElement('select');op.className='form-select';op.setAttribute('aria-label','Condition operator');
    const input=document.createElement('input');input.className='form-control';input.maxLength=300;input.value=rule.value || '';input.setAttribute('aria-label','Condition value');
    const remove=document.createElement('button');remove.type='button';remove.className='btn btn-outline-danger';remove.textContent='Remove';remove.addEventListener('click',()=>row.remove());
    function refresh(preferred) {
      const allowed=numeric.has(field.value)||dates.has(field.value)?['eq','ne','gte','lte','gt','lt','empty','not_empty']:['contains','not_contains','eq','ne','empty','not_empty'];
      op.replaceChildren(...allowed.map(key=>option(key,labels[key])));if(allowed.includes(preferred))op.value=preferred;
      input.type=dates.has(field.value)?'date':numeric.has(field.value)?'number':'text';input.step='any';toggle();
    }
    function toggle(){input.disabled=['empty','not_empty'].includes(op.value);input.required=!input.disabled;}
    field.addEventListener('change',()=>refresh(op.value));op.addEventListener('change',toggle);
    refresh(rule.op);row.append(field,op,input,remove);list.append(row);
  }
  let initial=[];try {initial=JSON.parse(hidden.value);} catch (_) {initial=[];}
  initial.forEach(add);
  document.getElementById('report-add-rule').addEventListener('click',()=>add());
  document.getElementById('report-all-time').addEventListener('click',()=>{form.elements.namedItem('from').value='';form.elements.namedItem('to').value='';});
  form.addEventListener('submit',()=>{
    hidden.value=JSON.stringify([...list.children].map(row=>({field:row.children[0].value,op:row.children[1].value,value:row.children[2].disabled?'':row.children[2].value})));
  });
})();
