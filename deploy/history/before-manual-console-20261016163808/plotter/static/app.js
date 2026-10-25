'use strict';
const $ = id => document.getElementById(id);
const checkButton = document.createElement('button');
checkButton.id='check';checkButton.className='text-button';checkButton.textContent='检查原点与配置（只读）';
checkButton.style.marginTop='16px';document.querySelector('.connect-row').after(checkButton);
let fonts = [], currentJob = null, downloadJob = null, state = {connected:false,phase:'disconnected',total:0,acknowledged:0}, busy = false;
let lastError = '', noticeTimer, revision = 0, actionRevision = 0;
let selectedFonts = [];
let axisRevision = 0, axisTimer;
const phases = {disconnected:'等待连接',idle:'机器已连接',running:'正在写字',paused:'已请求暂停 · 等待继续',draining:'等待机器完成动作',complete:'写字完成 · 已抬笔返回原点',failed:'任务中断 · 请检查控制器'};
function notice(message, error=false) {
  $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').hidden=false;
  clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('notice').hidden=true,error?10000:4500);
}
async function request(path, data) {
  const headers = {};
  const token = $('token').value.trim(); if(token) headers.Authorization='Bearer '+token;
  if(data!==undefined) headers['Content-Type']='application/json';
  const response = await fetch(path,{method:data===undefined?'GET':'POST',headers,body:data===undefined?undefined:JSON.stringify(data)});
  if(!response.ok) { const error=await response.json(); throw new Error(error.error || '请求失败'); }
  return response;
}
async function api(path,data) {return (await request(path,data)).json();}
function active(){return ['running','paused','draining'].includes(state.phase);}
function renderState(){
  $('connection').textContent=state.connected?'已连接 · '+state.port:'未连接机器';
  $('connection').classList.toggle('online',state.connected);
  $('mode').textContent=state.mode==='simulation'?'模拟模式 · 不落笔':'本地服务';
  $('connect').disabled=state.connected||busy; $('disconnect').disabled=!state.connected||active()||state.phase==='failed'||busy;
  $('check').disabled=!state.connected||active()||state.phase==='failed'||busy;
  $('start').disabled=!state.connected||!currentJob||!$('origin').checked||active()||busy||state.phase==='failed';
  $('pause').disabled=!['running','draining'].includes(state.phase)||busy;
  $('resume').disabled=state.phase!=='paused'||busy;
  $('phase').textContent=phases[state.phase]||state.phase;
  $('progressText').textContent=`${state.acknowledged} / ${state.total}`;
  $('progressFill').style.width=(state.total?100*state.acknowledged/state.total:0)+'%';
  $('machineStatus').textContent=state.status||'115200 baud · Z0 抬笔 / Z10 落笔';
  $('download').disabled=!downloadJob;
}
function invalidate(){revision++;currentJob=null;downloadJob=null;$('fontUsage').hidden=true;$('previewStatus').textContent='参数已修改，请重新生成路径后下载或写入。';$('count').textContent=$('text').value.length+' 字';renderState();if($('paperView').querySelector('svg'))$('phase').textContent=active()?(phases[state.phase]+' · 编辑内容未执行'):'内容已修改，请重新生成预览';}
function coordinateSettings(){return {reverse_x:$('reverseX').checked,reverse_y:$('reverseY').checked,swap_xy:$('swapXY').checked,origin:$('originCorner').value};}
function paperSettings(){const landscape=$('paper').value==='landscape';return {width:landscape?297:210,height:landscape?210:297,margin:Number($('margin').value),coordinate_system:coordinateSettings()};}
function renderCoordinateInfo(metadata){
  const [xd,yd]=metadata.axis_directions;
  $('axisSummary').textContent=`纸张原点：${metadata.origin_label} · X+ ${xd} · Y+ ${yd}`;
  const b=metadata.machine_bounds||metadata.machine_page_bounds;
  $('axisRange').textContent=`${metadata.machine_bounds?'路径':'纸张'}输出范围 / mm：X [${b[0]}, ${b[2]}] · Y [${b[1]}, ${b[3]}]`;
  $('originText').textContent=`纸张已放好，笔在纸张${metadata.origin_label}角，机器坐标为 (0,0,0)，X+ ${xd}、Y+ ${yd}。`;
}
function displayPaper(svg, metadata, preservePaths=false){
  const parsed=new DOMParser().parseFromString(svg,'image/svg+xml');
  const next=document.importNode(parsed.documentElement,true), previous=$('paperView').querySelector('svg');
  if(preservePaths&&previous&&previous.getAttribute('data-paper-width')===next.getAttribute('data-paper-width')&&previous.getAttribute('data-paper-height')===next.getAttribute('data-paper-height')){
    const writing=previous.querySelector('#writing-paths');if(writing)next.querySelector('#writing-paths').replaceWith(writing.cloneNode(true));
  }
  $('paperView').replaceChildren(next);
  $('paperView').style.aspectRatio=metadata.preview_size[0]+'/'+metadata.preview_size[1];
  $('paperLabel').textContent=`A4 / ${metadata.paper[0]} × ${metadata.paper[1]} mm`;
  renderCoordinateInfo(metadata);
}
async function refreshCoordinatePreview(){
  const requestRevision=++axisRevision, captured=revision;
  try{
    const guide=await api('/api/coordinate-preview',paperSettings());
    if(requestRevision!==axisRevision||captured!==revision)return;
    displayPaper(guide.svg,guide.metadata,true);
  }catch(error){if(requestRevision===axisRevision&&captured===revision)notice(error.message,true);}
}
function coordinateChanged(){invalidate();$('origin').checked=false;clearTimeout(axisTimer);axisTimer=setTimeout(refreshCoordinatePreview,150);}
function restoreCoordinateSettings(){
  try{
    const saved=JSON.parse(localStorage.getItem('plotter.coordinates.v1'));
    if(!saved||!['top-left','top-right','bottom-left','bottom-right'].includes(saved.origin)||['reverse_x','reverse_y','swap_xy'].some(key=>typeof saved[key]!=='boolean'))return;
    $('reverseX').checked=saved.reverse_x;$('reverseY').checked=saved.reverse_y;$('swapXY').checked=saved.swap_xy;$('originCorner').value=saved.origin;
  }catch(error){/* Browser storage may be unavailable. Default coordinates remain usable. */}
}
function updateProbabilities(){
  const total=selectedFonts.reduce((sum,f)=>sum+(Number.isFinite(f.weight)?f.weight:0),0);
  for(const [index,node] of [...$('fontMix').querySelectorAll('.font-probability')].entries())node.textContent=total>0?(100*selectedFonts[index].weight/total).toFixed(1)+'%':'—';
  $('addFont').disabled=!$('font').value||selectedFonts.length>=16||selectedFonts.some(f=>f.font_id===$('font').value);
  $('preview').textContent=selectedFonts.length>1?'生成 / 重新随机预览 ↗':'生成路径预览 ↗';
}
function renderFontMix(){
  $('fontMix').replaceChildren();
  for(const selected of selectedFonts){
    const font=fonts.find(f=>f.id===selected.font_id);
    const row=document.createElement('div');row.className='font-mix-row';
    const name=document.createElement('span');name.className='font-mix-name';name.textContent=font.name;name.title=font.name;
    const weight=document.createElement('input');weight.type='number';weight.min='0';weight.max='1000000';weight.step='any';weight.value=selected.weight;weight.setAttribute('aria-label',font.name+' 权重');
    weight.oninput=()=>{selected.weight=weight.value===''?0:Number(weight.value);invalidate();updateProbabilities();};
    const probability=document.createElement('span');probability.className='font-probability';
    const remove=document.createElement('button');remove.className='text-button font-remove';remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除 '+font.name);
    remove.onclick=()=>{selectedFonts=selectedFonts.filter(f=>f!==selected);invalidate();renderFontMix();};
    row.append(name,weight,probability,remove);$('fontMix').append(row);
  }
  if(!selectedFonts.length){const empty=document.createElement('p');empty.className='hint';empty.textContent='从上方添加至少一种字体。';$('fontMix').append(empty);}
  updateProbabilities();
}
function renderFontUsage(metadata){
  $('fontUsage').replaceChildren();
  const title=document.createElement('p');title.className='font-usage-title';title.textContent='本次预览的实际字体分布';$('fontUsage').append(title);
  for(const item of metadata.font_mix||[]){
    const row=document.createElement('div');row.className='font-usage-row';
    const name=document.createElement('span');name.textContent=fonts.find(f=>f.id===item.font_id)?.name||item.font;name.title=name.textContent;
    const count=document.createElement('span');count.textContent=`${item.used} 字 · ${(100*item.actual_probability).toFixed(1)}%`;row.append(name,count);$('fontUsage').append(row);
  }
  const note=document.createElement('p');note.className='hint';note.textContent=`${metadata.fallback_characters||0} 个字符因部分字体缺字而重分配。下载及写入使用当前预览；再次生成会重新随机。`;$('fontUsage').append(note);$('fontUsage').hidden=false;
}
function filterFonts(){
  const selected=$('font').value, query=$('fontSearch').value.toLowerCase();
  $('font').replaceChildren();
  for(const font of fonts.filter(f=>f.name.toLowerCase().includes(query))) {
    const option=document.createElement('option');option.value=font.id;option.textContent=font.name;$('font').append(option);
  }
  if([...$('font').options].some(o=>o.value===selected))$('font').value=selected;
  updateProbabilities();
}
async function loadPorts(){
  const result=await api('/api/ports');const old=$('port').value;$('port').replaceChildren();
  for(const port of result.ports){const option=document.createElement('option');option.value=port.device;option.textContent=port.device+' · '+port.description;$('port').append(option);}
  if(result.ports.some(p=>p.device===old))$('port').value=old;
  else if(result.ports.some(p=>p.device==='COM5'))$('port').value='COM5';
}
async function load(){
  const result=await api('/api/fonts');fonts=result.fonts;filterFonts();
  const defaultFont=fonts.find(f=>f.name==='blood8-2_kvenjoy_blood8');if(defaultFont)$('font').value=defaultFont.id;
  selectedFonts=selectedFonts.filter(f=>fonts.some(font=>font.id===f.font_id));
  if(!selectedFonts.length&&$('font').value)selectedFonts=[{font_id:$('font').value,weight:100}];
  renderFontMix();
  state=await api('/api/state');renderState();await loadPorts();
  await refreshCoordinatePreview();
}
async function action(task){if(busy)return;actionRevision++;busy=true;renderState();try{await task();}catch(error){notice(error.message,true);}finally{busy=false;renderState();}}
$('preview').onclick=()=>action(async()=>{
  const landscape=$('paper').value==='landscape';
  if(!selectedFonts.length||!selectedFonts.some(f=>f.weight>0)||selectedFonts.some(f=>!Number.isFinite(f.weight)||f.weight<0||f.weight>1000000))throw new Error('请添加字体并设置有效权重，至少一种字体的权重须大于 0');
  const data={...paperSettings(),font_mix:selectedFonts.map(f=>({...f})),text:$('text').value};
  for(const key of ['margin','size','pitch','spacing','feed'])data[key]=Number($(key).value);
  const capturedRevision=revision;
  $('preview').disabled=true;
  try{
    const result=await api('/api/preview',data);
    if(capturedRevision!==revision){notice('生成期间内容发生了修改，请重新生成预览');return;}
    currentJob=result.job_id;downloadJob=result.job_id;
    axisRevision++;clearTimeout(axisTimer);displayPaper(result.svg,result.metadata);
    $('previewStatus').textContent='当前路径已生成，下载与写入使用此预览的坐标设置。';
    $('paperLabel').textContent=`A4 / ${data.width} × ${data.height} mm`;
    const m=result.metadata;$('metrics').replaceChildren();
    for(const value of [`${m.lines.length} 行 · ${m.strokes} 笔画`,`${m.commands} 条指令`,`${m.xy_mm} mm 路程`]){const span=document.createElement('span');span.textContent=value;$('metrics').append(span);}
    renderFontUsage(m);
    notice('路径已生成，可以下载或开始写字');
  }finally{$('preview').disabled=false;}
});
$('download').onclick=()=>action(async()=>{
  const response=await request(`/api/jobs/${downloadJob}/gcode`),blob=await response.blob(),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='writing.gcode';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('refresh').onclick=()=>action(loadPorts);
$('authenticate').onclick=()=>action(load);
$('connect').onclick=()=>action(async()=>{state=await api('/api/connect',{port:$('port').value});notice('机器已连接');});
$('disconnect').onclick=()=>action(async()=>{state=await api('/api/disconnect',{});});
$('check').onclick=()=>action(async()=>{state=await api('/api/check',{job_id:currentJob});notice('原点与机器配置检查通过');});
$('start').onclick=()=>action(async()=>{state=await api('/api/start',{job_id:currentJob,origin_confirmed:$('origin').checked});currentJob=null;$('origin').checked=false;notice(state.mode==='simulation'?'模拟任务开始':'写字任务开始');});
$('pause').onclick=()=>action(async()=>{state=await api('/api/pause',{});});
$('resume').onclick=()=>action(async()=>{state=await api('/api/resume',{});});
$('origin').onchange=renderState;
for(const key of ['reverseX','reverseY','swapXY','originCorner'])$(key).addEventListener('change',coordinateChanged);
$('saveCoordinates').onclick=()=>{
  try{localStorage.setItem('plotter.coordinates.v1',JSON.stringify(coordinateSettings()));notice('坐标设置已保存到当前浏览器');}
  catch(error){notice('浏览器不允许保存设置，当前设置仍可使用',true);}
};
$('resetCoordinates').onclick=()=>{$('reverseX').checked=false;$('reverseY').checked=false;$('swapXY').checked=false;$('originCorner').value='top-left';coordinateChanged();};
$('addFont').onclick=()=>{
  if(!$('font').value||selectedFonts.length>=16||selectedFonts.some(f=>f.font_id===$('font').value))return;
  selectedFonts.push({font_id:$('font').value,weight:100});invalidate();renderFontMix();
};
$('font').onchange=updateProbabilities;
$('fontSearch').oninput=filterFonts;
for(const key of ['text','size','pitch','spacing','feed'])$(key).addEventListener('input',invalidate);
for(const key of ['paper','margin'])$(key).addEventListener('input',coordinateChanged);
async function poll(){
  try{if(!busy){const capturedAction=actionRevision;const result=await api('/api/state');if(capturedAction===actionRevision&&!busy){state=result;renderState();if(state.error&&state.error!==lastError){lastError=state.error;notice(state.error,true);}}}}
  catch(error){if(error.message!==lastError){lastError=error.message;notice('服务连接异常：'+error.message,true);}}
  finally{setTimeout(poll,800);}
}
$('count').textContent=$('text').value.length+' 字';
restoreCoordinateSettings();
load().catch(error=>notice(error.message,true)).finally(poll);
