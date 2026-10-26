'use strict';
const $ = id => document.getElementById(id);
const checkButton = document.createElement('button');
checkButton.id='check';checkButton.className='text-button';checkButton.textContent='检查原点与配置（只读）';
checkButton.style.marginTop='16px';document.querySelector('.connect-row').after(checkButton);
let fonts = [], currentJob = null, downloadJob = null, state = {connected:false,phase:'disconnected',total:0,acknowledged:0}, busy = false;
let lastError = '', noticeTimer, revision = 0, actionRevision = 0;
let selectedFonts = [];
let axisRevision = 0, axisTimer;
let observedMotionRevision = null;
const layoutFields=['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size','spacing','feed'];
const presetStorage='plotter.layout-presets.v1';
const defaultLayout={width:210,height:297,marginTop:30,marginBottom:30,marginLeft:30,marginRight:30,rows:20,size:5,spacing:.9,feed:1800};
const builtinPresets=[{id:'a4-portrait',name:'A4 竖向 · 210 × 297',values:{...defaultLayout}},
  {id:'a4-landscape',name:'A4 横向 · 297 × 210',values:{...defaultLayout,width:297,height:210}}];
let layoutPresets=[], selectedPreset='a4-portrait', deletedPreset=null;
const phases = {disconnected:'等待连接',idle:'机器已连接',running:'正在写字',paused:'已请求暂停 · 等待继续',draining:'等待机器完成动作',complete:'写字完成 · 已抬笔返回原点',failed:'任务中断 · 请检查控制器',manual:'手动动作中',manual_stopping:'正在停止手动动作',stopping:'正在终止写字任务',stopped:'任务已终止 · 请重新连接并定位原点'};
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
function writing(){return state.operation_type==='writing'||['running','paused','draining','stopping'].includes(state.phase);}
function active(){return writing()||['manual','manual_stopping'].includes(state.phase);}
function renderState(){
  if(observedMotionRevision!==null&&state.motion_revision!==undefined&&observedMotionRevision!==state.motion_revision){
    $('origin').checked=false;
    $('previewStatus').textContent='笔位置已调整，预览保留；请确认当前位置作为本次原点。';
  }
  if(state.motion_revision!==undefined)observedMotionRevision=state.motion_revision;
  $('connection').textContent=state.connected?'已连接 · '+state.port:'未连接机器';
  $('connection').classList.toggle('online',state.connected);
  $('mode').textContent=state.mode==='simulation'?'模拟模式 · 不落笔':'本地服务';
  $('connect').disabled=state.connected||busy; $('disconnect').disabled=!state.connected||active()||state.phase==='failed'||busy;
  $('check').disabled=!state.connected||active()||state.phase==='failed'||busy;
  $('start').disabled=!state.connected||!currentJob||!$('origin').checked||active()||busy||state.phase==='failed';
  $('pause').disabled=!writing()||!['running','draining'].includes(state.phase);
  $('resume').disabled=state.phase!=='paused';
  $('stop').disabled=!writing()||state.phase==='stopping';
  const manual=['manual','manual_stopping'].includes(state.phase), controllerIdle=state.mode==='simulation'||(state.status||'').startsWith('<Idle');
  const writable=state.connected&&controllerIdle&&!active()&&!busy&&['idle','complete'].includes(state.phase);
  $('manualControls').disabled=writing();
  $('consoleLock').textContent=writing()?'写字任务中 · 已锁定':state.connected?'手动控制可用':'连接机器后可用';
  for(const key of ['penUp','penDown','jogDistance','jogFeed'])$(key).disabled=!writable;
  $('penUp').disabled=!writable||state.pen_top!==false;
  $('penUp').textContent=state.pen_top===true?'已在顶部':'抬笔 / Z0';
  $('penUp').title=state.pen_top===true?'控制器报告 Z0，已禁用重复抬笔':'读取最新 Z 坐标后抬笔';
  for(const button of document.querySelectorAll('[data-jog-axis],[data-step]'))button.disabled=!writable;
  $('manualPause').disabled=!manual||state.phase==='manual_stopping';
  $('manualStop').disabled=!manual||state.phase==='manual_stopping';
  $('consolePosition').textContent=state.position?`Z ${state.position[2].toFixed(3)} mm`:'Z —';
  $('penState').textContent=({up:'顶部 · Z0',down:'控制器 Z10',unknown:'笔位置待确认'})[state.pen||'unknown'];
  $('consoleLastAction').textContent=state.last_action||'单击移动一次，方向按机器输出轴，见预览箭头。暂停会取消本次微动。';
  $('phase').textContent=phases[state.phase]||state.phase;
  $('progressText').textContent=`${state.acknowledged} / ${state.total}`;
  $('progressFill').style.width=(state.total?100*state.acknowledged/state.total:0)+'%';
  $('machineStatus').textContent=state.status?`${(state.status.match(/^<([^|>]+)/)||[])[1]||'未知状态'} · Z0 抬笔 / Z10 落笔`:'115200 baud · Z0 抬笔 / Z10 落笔';
  $('download').disabled=!downloadJob;
}
function invalidate(){revision++;$('origin').checked=false;currentJob=null;downloadJob=null;$('fontUsage').hidden=true;$('previewStatus').textContent='参数已修改，请重新生成路径后下载或写入。';$('count').textContent=$('text').value.length+' 字';renderState();if($('paperView').querySelector('svg'))$('phase').textContent=active()?(phases[state.phase]+' · 编辑内容未执行'):'内容已修改，请重新生成预览';}
function coordinateSettings(){return {reverse_x:$('reverseX').checked,reverse_y:$('reverseY').checked,swap_xy:$('swapXY').checked,origin:$('originCorner').value};}
function paperSettings(){return {width:Number($('width').value),height:Number($('height').value),margin_top:Number($('marginTop').value),margin_bottom:Number($('marginBottom').value),margin_left:Number($('marginLeft').value),margin_right:Number($('marginRight').value),rows:Number($('rows').value),size:Number($('size').value),coordinate_system:coordinateSettings()};}
function renderCoordinateInfo(metadata){
  const [xd,yd]=metadata.axis_directions;
  $('axisSummary').textContent=`本次原点：${metadata.origin_label} · X+ ${xd} · Y+ ${yd}`;
  const b=metadata.machine_bounds||metadata.machine_page_bounds;
  $('axisRange').textContent=`${metadata.machine_bounds?'路径':'纸张'}输出范围 / mm：X [${b[0]}, ${b[2]}] · Y [${b[1]}, ${b[3]}]`;
  $('originText').textContent=`已将笔移至所测范围${metadata.origin_label}角并抬笔至 Z0；当前位置作为本次原点，X+ ${xd}、Y+ ${yd}。`;
}
function displayPaper(svg, metadata, preservePaths=false){
  const parsed=new DOMParser().parseFromString(svg,'image/svg+xml');
  const next=document.importNode(parsed.documentElement,true), previous=$('paperView').querySelector('svg');
  if(preservePaths&&previous&&previous.getAttribute('data-paper-width')===next.getAttribute('data-paper-width')&&previous.getAttribute('data-paper-height')===next.getAttribute('data-paper-height')){
    const writing=previous.querySelector('#writing-paths');if(writing)next.querySelector('#writing-paths').replaceWith(writing.cloneNode(true));
  }
  $('paperView').replaceChildren(next);
  $('paperView').style.aspectRatio=metadata.preview_size[0]+'/'+metadata.preview_size[1];
  $('paperLabel').textContent=`范围 / ${metadata.paper[0]} × ${metadata.paper[1]} mm`;
  renderCoordinateInfo(metadata);
  if(metadata.writable)$('layoutSummary').textContent=`可写范围 ${metadata.writable[0].toFixed(1)} × ${metadata.writable[1].toFixed(1)} mm · ${metadata.rows} 行 · 自动行距 ${metadata.pitch.toFixed(2)} mm`;

}
async function refreshCoordinatePreview(){
  const requestRevision=++axisRevision, captured=revision;
  try{
    const guide=await api('/api/coordinate-preview',paperSettings());
    if(requestRevision!==axisRevision||captured!==revision)return;
    displayPaper(guide.svg,guide.metadata);
  }catch(error){if(requestRevision===axisRevision&&captured===revision)notice(error.message,true);}
}
function coordinateChanged(){invalidate();$('origin').checked=false;clearTimeout(axisTimer);axisTimer=setTimeout(refreshCoordinatePreview,150);}
function findPreset(){return [...builtinPresets,...layoutPresets].find(p=>p.id===selectedPreset);}
function validPresetValues(values){
  if(!values||typeof values!=='object')return false;
  for(const key of layoutFields){
    const value=values[key], input=$(key);
    if(typeof value!=='number'||!Number.isFinite(value)||value<Number(input.min)||value>Number(input.max))return false;
  }
  return Number.isInteger(values.rows)&&values.width>values.marginLeft+values.marginRight&&
    values.height-values.marginTop-values.marginBottom>=values.rows*values.size;
}
function currentLayout(){return Object.fromEntries(layoutFields.map(key=>[key,Number($(key).value)]));}
function renderPresets(){
  $('layoutPreset').replaceChildren();
  for(const preset of [...builtinPresets,...layoutPresets]){
    const option=document.createElement('option');option.value=preset.id;option.textContent=preset.name;$('layoutPreset').append(option);
  }
  $('layoutPreset').value=selectedPreset;
  const custom=layoutPresets.some(p=>p.id===selectedPreset);
  $('updatePreset').disabled=!custom;$('deletePreset').disabled=!custom;
  $('undoPreset').hidden=!deletedPreset;
  updatePresetHint();
}
function updatePresetHint(){
  const preset=findPreset(), values=currentLayout();
  const dirty=preset&&layoutFields.some(key=>values[key]!==preset.values[key]);
  $('presetHint').textContent=(dirty?'参数已修改，尚未保存。':'当前参数已载入预设。')+
    '保存宽高、四边边距、字高、行数、字间距和速度；仅保存在当前浏览器。';
}
function storePresets(presets=layoutPresets, selected=selectedPreset){
  localStorage.setItem(presetStorage,JSON.stringify({presets,selected}));
}
function applyPreset(preset){
  selectedPreset=preset.id;
  for(const key of layoutFields)$(key).value=preset.values[key];
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';
  renderPresets();coordinateChanged();
}
function restorePresets(){
  try{
    const saved=JSON.parse(localStorage.getItem(presetStorage));
    if(saved&&Array.isArray(saved.presets)){
      const ids=new Set(builtinPresets.map(p=>p.id)), names=new Set();
      layoutPresets=saved.presets.slice(0,50).filter(p=>{
        if(!p||typeof p.id!=='string'||!p.id||p.id.length>100||ids.has(p.id)||typeof p.name!=='string'||!p.name.trim()||p.name.length>40||names.has(p.name)||!validPresetValues(p.values))return false;
        ids.add(p.id);names.add(p.name);return true;
      });
      if([...builtinPresets,...layoutPresets].some(p=>p.id===saved.selected))selectedPreset=saved.selected;
    }
  }catch(error){/* Invalid or unavailable browser storage leaves built-in presets usable. */}
  const preset=findPreset();
  for(const key of layoutFields)$(key).value=preset.values[key];
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';renderPresets();
}
async function saveLayoutPreset(update=false){
  const name=$('presetName').value.trim(), values=currentLayout(), capturedPreset=selectedPreset;
  if(!name||name.length>40)throw new Error('请输入 1–40 字的预设名称');
  if(!layoutFields.every(key=>$(key).value!==''&&$(key).checkValidity())||!validPresetValues(values))throw new Error('排版参数无效，请检查宽高、边距、行数和字大小');
  if(layoutPresets.some(p=>p.name===name&&(!update||p.id!==selectedPreset)))throw new Error('已有同名预设，请选择它后点击“更新当前预设”');
  if(update&&!layoutPresets.some(p=>p.id===selectedPreset))throw new Error('内置预设请另存为新预设');
  if(!update&&layoutPresets.length>=50)throw new Error('最多保存 50 个预设，请先删除不用的预设');
  await api('/api/coordinate-preview',paperSettings());
  // Capture values before validation, and refuse to save if an edit raced it.
  if(selectedPreset!==capturedPreset||layoutFields.some(key=>Number($(key).value)!==values[key])||$('presetName').value.trim()!==name)throw new Error('保存期间参数已变化，请重新保存');
  const preset={id:update?selectedPreset:'preset-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,values};
  const next=update?layoutPresets.map(p=>p.id===selectedPreset?preset:p):[...layoutPresets,preset];
  try{storePresets(next,preset.id);}catch(error){throw new Error('浏览器不允许保存预设，请检查浏览器存储设置');}
  layoutPresets=next;selectedPreset=preset.id;deletedPreset=null;renderPresets();notice(update?'当前预设已更新':'新预设已保存，刷新后仍可使用');
}
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
async function realtimeAction(task){actionRevision++;try{await task();}catch(error){notice(error.message,true);}finally{renderState();}}
$('preview').onclick=()=>action(async()=>{
  if(!selectedFonts.length||!selectedFonts.some(f=>f.weight>0)||selectedFonts.some(f=>!Number.isFinite(f.weight)||f.weight<0||f.weight>1000000))throw new Error('请添加字体并设置有效权重，至少一种字体的权重须大于 0');
  const data={...paperSettings(),font_mix:selectedFonts.map(f=>({...f})),text:$('text').value};
  for(const key of ['spacing','feed'])data[key]=Number($(key).value);
  const capturedRevision=revision;
  $('preview').disabled=true;
  try{
    const result=await api('/api/preview',data);
    if(capturedRevision!==revision){notice('生成期间内容发生了修改，请重新生成预览');return;}
    if(result.metadata.motion_revision!==undefined){state.motion_revision=result.metadata.motion_revision;observedMotionRevision=result.metadata.motion_revision;}
    currentJob=result.job_id;downloadJob=result.job_id;
    axisRevision++;clearTimeout(axisTimer);displayPaper(result.svg,result.metadata);
    $('previewStatus').textContent='当前路径已生成，下载与写入使用此预览的坐标设置。';
    $('paperLabel').textContent=`范围 / ${data.width} × ${data.height} mm`;
    const m=result.metadata;$('metrics').replaceChildren();
    for(const value of [`${m.lines.length} / ${m.rows} 行 · ${m.strokes} 笔画`,`${m.commands} 条指令`,`${m.xy_mm} mm 路程`]){const span=document.createElement('span');span.textContent=value;$('metrics').append(span);}
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
$('pause').onclick=()=>realtimeAction(async()=>{state=await api('/api/pause',{});});
$('resume').onclick=()=>realtimeAction(async()=>{state=await api('/api/resume',{});});
$('stop').onclick=()=>realtimeAction(async()=>{state=await api('/api/stop',{});$('origin').checked=false;currentJob=null;notice('已请求终止；完成后需重新连接并定位原点');});
$('penUp').onclick=()=>action(async()=>{state=await api('/api/pen',{position:'up'});});
$('penDown').onclick=()=>action(async()=>{state=await api('/api/pen',{position:'down'});});
$('manualPause').onclick=()=>realtimeAction(async()=>{state=await api('/api/manual-pause',{});});
$('manualStop').onclick=()=>realtimeAction(async()=>{state=await api('/api/manual-stop',{});});
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>{$('jogDistance').value=button.dataset.step;};
for(const button of document.querySelectorAll('[data-jog-axis]'))button.onclick=()=>action(async()=>{
  const distance=Number($('jogDistance').value),feed=Number($('jogFeed').value);
  if(!Number.isFinite(distance)||distance<.01||distance>20)throw new Error('单次微动距离须为 0.01–20 mm');
  if(!Number.isFinite(feed)||feed<50||feed>1000)throw new Error('微动速度须为 50–1000 mm/min');
  state=await api('/api/jog',{axis:button.dataset.jogAxis,distance:distance*Number(button.dataset.jogSign),feed});
});
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
for(const key of ['text','spacing','feed'])$(key).addEventListener('input',invalidate);
for(const key of ['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size'])$(key).addEventListener('input',coordinateChanged);
for(const key of layoutFields)$(key).addEventListener('input',updatePresetHint);
$('layoutPreset').onchange=()=>{
  const preset=[...builtinPresets,...layoutPresets].find(p=>p.id===$('layoutPreset').value);
  if(!preset)return;
  applyPreset(preset);
  try{storePresets();}catch(error){notice('已应用预设，但浏览器未能记住本次选择',true);}
};
$('savePreset').onclick=()=>action(()=>saveLayoutPreset());
$('updatePreset').onclick=()=>action(()=>saveLayoutPreset(true));
$('deletePreset').onclick=()=>{
  const preset=layoutPresets.find(p=>p.id===selectedPreset);if(!preset)return;
  const next=layoutPresets.filter(p=>p!==preset);
  try{storePresets(next,'a4-portrait');}catch(error){notice('删除未保存，请检查浏览器存储设置',true);return;}
  deletedPreset={preset,index:layoutPresets.indexOf(preset)};layoutPresets=next;applyPreset(builtinPresets[0]);notice('预设已删除，可点击“撤销删除”恢复');
};
$('undoPreset').onclick=()=>{
  if(!deletedPreset)return;
  const next=[...layoutPresets];next.splice(deletedPreset.index,0,deletedPreset.preset);
  try{storePresets(next,deletedPreset.preset.id);}catch(error){notice('恢复未保存，请检查浏览器存储设置',true);return;}
  const preset=deletedPreset.preset;layoutPresets=next;deletedPreset=null;applyPreset(preset);notice('预设已恢复');
};
async function poll(){
  try{if(!busy){const capturedAction=actionRevision;const result=await api('/api/state');if(capturedAction===actionRevision&&!busy){state=result;renderState();if(state.error&&state.error!==lastError){lastError=state.error;notice(state.error,true);}}}}
  catch(error){if(error.message!==lastError){lastError=error.message;notice('服务连接异常：'+error.message,true);}}
  finally{setTimeout(poll,800);}
}
$('count').textContent=$('text').value.length+' 字';
restoreCoordinateSettings();
restorePresets();
load().catch(error=>notice(error.message,true)).finally(poll);
