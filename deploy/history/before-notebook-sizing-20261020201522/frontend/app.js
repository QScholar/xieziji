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
let speedTimer, speedPending=null, speedSending=false, speedJob=null;
const layoutFields=['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size','spacing','feed','moveFeed','punctuationWidth','gridColumns','gridRowGap','gridColumnGap','gridSizePercent','gridPadding'];
let presetStorage='plotter.layout-presets.v1';
let layoutMode='', modeDrafts={}, sourcePages=[$('text').value], sourceIndex=0, documentResult=null, outputIndex=0;
const startedJobs=new Set();
const humanFields={vertical:'Vertical',scale:'Scale',rotation:'Rotation',spacing:'Spacing',baseline:'Baseline'};
const humanDefault={enabled:false,vertical:.3,scale:5,rotation:2,spacing:.2,baseline:.4,
  vertical_enabled:true,scale_enabled:true,rotation_enabled:true,spacing_enabled:true,baseline_enabled:true};
const humanLevels={light:[.15,3,1,.1,.2],medium:[.3,5,2,.2,.4],strong:[.6,10,4,.4,.8]};
const defaultLayout={width:210,height:297,marginTop:30,marginBottom:30,marginLeft:30,marginRight:30,rows:20,size:5,spacing:.9,feed:1800,moveFeed:3000,punctuationWidth:.8,gridColumns:20,gridRowGap:2.9,gridColumnGap:0,gridSizePercent:80,gridPadding:.3};
const notebookPresets=[{id:'a4-portrait',name:'A4 竖向 · 210 × 297',values:{...defaultLayout}},
  {id:'a4-landscape',name:'A4 横向 · 297 × 210',values:{...defaultLayout,width:297,height:210}}];
const gridPresets=[{id:'a4-grid',name:'A4 方格 · 20 行 × 20 列',values:{...defaultLayout,marginTop:41,marginBottom:41,marginLeft:26.5,marginRight:26.5}}];
let builtinPresets=notebookPresets;
let layoutPresets=[], selectedPreset='a4-portrait', deletedPreset=null;
const fontPresetStorage='plotter.font-presets.v1';
let fontPresets=[], selectedFontPreset='', deletedFontPreset=null, fontPresetsLoaded=false;
const phases = {disconnected:'等待连接',idle:'机器已连接',running:'正在写字',paused:'已请求暂停 · 等待继续',draining:'等待机器完成动作',complete:'写字完成 · 已抬笔返回原点',failed:'任务中断 · 请检查控制器',manual:'手动动作中',manual_stopping:'正在停止手动动作',stopping:'正在减速终止并清空路径',returning:'正在抬笔并返回本次书写原点',stopped:'任务已终止 · 已抬笔返回本次原点'};
function notice(message, error=false) {
  $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').hidden=false;
  clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('notice').hidden=true,error?10000:4500);
}
async function request(path, data) {
  const headers = {};
  const token = $('token').value.trim(); if(token) headers.Authorization='Bearer '+token;
  if(data!==undefined) headers['Content-Type']='application/json';
  const base=(window.PLOTTER_API_BASE||'').replace(/\/$/,'');
  const response = await fetch(base+path,{method:data===undefined?'GET':'POST',headers,body:data===undefined?undefined:JSON.stringify(data)});
  if(!response.ok) { const error=await response.json(); throw new Error(error.error || '请求失败'); }
  return response;
}
async function api(path,data) {return (await request(path,data)).json();}
function writing(){return state.operation_type==='writing'||['running','paused','draining','stopping','returning'].includes(state.phase);}
function active(){return writing()||['manual','manual_stopping'].includes(state.phase);}
function formatEtaDuration(seconds){
  const minutes=Math.max(1,Math.ceil(seconds/60));
  if(seconds<60)return '不足 1 分钟';
  return minutes>=60?`${Math.floor(minutes/60)} 小时${minutes%60?' '+minutes%60+' 分钟':''}`:`${minutes} 分钟`;
}
function renderEta(){
  const eta=state.eta||{},remaining=eta.remaining_seconds;
  let title='预计剩余 —',detail='开始任务后显示预计剩余时间。';
  if(!state.connected&&state.job_id&&state.phase!=='disconnected'){title='ETA 暂不可用';detail='连接恢复后更新估算。';}
  else if(state.phase==='complete'){title='任务已完成';detail='已收到机器完成确认。';}
  else if(['stopping','returning','stopped','failed'].includes(state.phase)){detail='任务已终止或正在回位，不再估算书写时间。';}
  else if(state.phase==='paused'){
    title=`已暂停${Number.isFinite(remaining)?' · 剩余约 '+formatEtaDuration(remaining):''}`;
    detail='继续后更新预计完成时间，暂停时间不计入书写速度。';
  }else if(eta.status==='draining'){
    title='机器收尾中';detail=Number.isFinite(remaining)?`预计还需 ${formatEtaDuration(remaining)}，等待机器确认完成。`:'路径已发送，等待机器确认完成。';
  }else if(['running','draining'].includes(state.phase)){
    if(Number.isFinite(remaining)&&remaining>0){
      title='预计剩余约 '+formatEtaDuration(remaining);
      const finish=Number.isFinite(eta.estimated_finish_at)?new Date(eta.estimated_finish_at*1000):null;
      detail=(finish?`预计完成 ${finish.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})} · `:'')+
        (eta.source==='observed'?'随实际进度和调速自动更新。':'按路径初估，运行后自动校准。');
    }else{title='正在估算剩余时间…';detail='采集实际进度后显示 ETA。';}
  }
  $('taskEta').textContent=title;$('etaDetail').textContent=detail;
}
function canAdjustSpeed(){return state.connected&&['running','paused','draining'].includes(state.phase)&&Number.isInteger(state.feed_override);}
function renderSpeed(){
  const enabled=canAdjustSpeed();
  if(speedJob!==state.job_id||!enabled){clearTimeout(speedTimer);speedPending=null;speedJob=state.job_id;}
  $('liveSpeed').disabled=!enabled;$('resetSpeed').disabled=!enabled;
  $('liveSpeed').max=state.speed_max_percent??200;
  if(speedPending===null&&!speedSending){
    $('liveSpeed').value=state.feed_override_requested??100;
    $('liveSpeedValue').textContent=$('liveSpeed').value+'%';
  }
  const limit=state.speed_limit??10000;
  $('liveSpeedStatus').textContent=state.speed_limit_update_pending?'当前任务继续运行；结束后自动启用 12000 mm/min 上限，更新期间暂不启动新任务。':enabled?`控制器已应用 ${state.feed_override}%${state.feed_override!==state.feed_override_requested?' · 正在调整至 '+state.feed_override_requested+'%':''}。最高 ${limit} mm/min，倍率也影响移笔和抬落笔。`:
    writing()?'当前阶段或固件不支持实时调速。':'书写或暂停时可拖动调速，无需重新生成路径；每个新任务恢复 100%。';
}
async function sendSpeed(){
  if(speedSending||speedPending===null||!canAdjustSpeed())return;
  const percent=speedPending,jobId=speedJob;speedPending=null;speedSending=true;actionRevision++;
  const capturedAction=actionRevision;
  try{
    const result=await api('/api/speed',{percent,job_id:jobId});
    if(state.job_id===jobId&&actionRevision===capturedAction)state=result;
  }catch(error){speedPending=null;notice('调速未成功：'+error.message,true);}
  finally{actionRevision++;speedSending=false;renderState();if(speedPending!==null)speedTimer=setTimeout(sendSpeed,120);}
}
function queueSpeed(percent){
  if(!canAdjustSpeed())return;
  percent=Math.max(10,Math.min(state.speed_max_percent??200,percent));
  speedJob=state.job_id;speedPending=percent;
  $('liveSpeed').value=percent;$('liveSpeedValue').textContent=percent+'%';
  clearTimeout(speedTimer);speedTimer=setTimeout(sendSpeed,120);
}
$('liveSpeed').addEventListener('input',()=>queueSpeed(Number($('liveSpeed').value)));
$('resetSpeed').onclick=()=>queueSpeed(100);
function renderState(){
  renderSpeed();
  renderEta();
  if(observedMotionRevision!==null&&state.motion_revision!==undefined&&observedMotionRevision!==state.motion_revision){
    $('origin').checked=false;
    $('previewStatus').textContent='笔位置已调整，预览保留；请确认当前位置作为本次原点。';
  }
  if(state.motion_revision!==undefined)observedMotionRevision=state.motion_revision;
  $('connection').textContent=state.connected?'已连接 · '+state.port:'未连接机器';
  $('connection').classList.toggle('online',state.connected);
  $('mode').textContent=state.mode==='simulation'?'模拟模式 · 不落笔':'C++ 控制服务';
  $('connect').disabled=state.connected||busy||state.speed_limit_update_pending===true; $('disconnect').disabled=!state.connected||active()||state.phase==='failed'||busy;
  $('check').disabled=!state.connected||active()||state.phase==='failed'||busy||state.speed_limit_update_pending===true;
  $('start').disabled=!state.connected||!currentJob||!$('origin').checked||active()||busy||state.phase==='failed'||state.speed_limit_update_pending===true;
  $('pause').disabled=!writing()||!['running','draining'].includes(state.phase);
  $('resume').disabled=state.phase!=='paused';
  $('stop').disabled=!writing()||state.phase==='stopping';
  $('stop').textContent=state.phase==='returning'?'停止回原点':'终止任务';
  const manual=['manual','manual_stopping'].includes(state.phase), controllerIdle=state.mode==='simulation'||(state.status||'').startsWith('<Idle');
  const writable=state.connected&&controllerIdle&&!active()&&!busy&&!state.speed_limit_update_pending&&['idle','complete','stopped'].includes(state.phase);
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
  $('phase').textContent=state.phase==='stopped'&&state.returned_to_origin!==true?'回原点已取消 · 请重新连接并确认位置':phases[state.phase]||state.phase;
  $('progressText').textContent=`${state.acknowledged} / ${state.total}`;
  $('progressFill').style.width=(state.total?100*state.acknowledged/state.total:0)+'%';
  const actualFeed=(state.status||'').match(/\|FS:([\d.]+),/);
  $('machineStatus').textContent=state.status?`${(state.status.match(/^<([^|>]+)/)||[])[1]||'未知状态'}${actualFeed?' · 瞬时进给 '+actualFeed[1]+' mm/min':''} · Z0 抬笔 / Z10 落笔`:'115200 baud · Z0 抬笔 / Z10 落笔';
  $('download').disabled=!downloadJob;
  $('downloadAll').disabled=!documentResult||busy;
  $('rerandomize').disabled=busy;
}
function invalidate(){sourcePages[sourceIndex]=$('text').value;revision++;$('origin').checked=false;currentJob=null;downloadJob=null;documentResult=null;renderOutputNavigation();$('fontUsage').hidden=true;$('previewStatus').textContent='参数已修改，请重新生成全部页面后下载或写入。';updateCount();renderState();if($('paperView').querySelector('svg'))$('phase').textContent=active()?(phases[state.phase]+' · 编辑内容未执行'):'内容已修改，请重新生成预览';}
function coordinateSettings(){return {reverse_x:$('reverseX').checked,reverse_y:$('reverseY').checked,swap_xy:$('swapXY').checked,origin:$('originCorner').value};}
function paperSettings(){return {layout_mode:layoutMode||'notebook',pagination:$('pagination').value,width:Number($('width').value),height:Number($('height').value),margin_top:Number($('marginTop').value),margin_bottom:Number($('marginBottom').value),margin_left:Number($('marginLeft').value),margin_right:Number($('marginRight').value),rows:Number($('rows').value),size:Number($('size').value),punctuation_width:Number($('punctuationWidth').value),grid:{columns:Number($('gridColumns').value),row_gap:Number($('gridRowGap').value),column_gap:Number($('gridColumnGap').value),size_percent:Number($('gridSizePercent').value),padding:Number($('gridPadding').value),draw_lines:$('gridDrawLines').checked},coordinate_system:coordinateSettings(),humanization:currentHumanization()};}
function currentHumanization(){
  const result={enabled:$('humanEnabled').checked};
  for(const [key,suffix] of Object.entries(humanFields)){result[key]=Number($('human'+suffix).value);result[key+'_enabled']=$('human'+suffix+'Enabled').checked;}
  return result;
}
function validHumanization(value){
  return value&&typeof value==='object'&&typeof value.enabled==='boolean'&&Object.entries(humanFields).every(([key,suffix])=>
    typeof value[key+'_enabled']==='boolean'&&typeof value[key]==='number'&&Number.isFinite(value[key])&&value[key]>=0&&value[key]<=Number($('human'+suffix).max));
}
function sameHumanization(a,b){return ['enabled',...Object.keys(humanFields).flatMap(key=>[key,key+'_enabled'])].every(key=>a[key]===b[key]);}
function setHumanization(value=humanDefault){
  $('humanEnabled').checked=value.enabled;
  for(const [key,suffix] of Object.entries(humanFields)){$('human'+suffix).value=value[key];$('human'+suffix+'Enabled').checked=value[key+'_enabled'];}
  renderHumanization();
}
function renderHumanization(){
  const value=currentHumanization();$('humanParams').disabled=!value.enabled;
  for(const [key,suffix] of Object.entries(humanFields))$('human'+suffix).disabled=!value.enabled||!value[key+'_enabled'];
  const level=Object.entries(humanLevels).find(([name,values])=>Object.keys(humanFields).every((key,i)=>value[key+'_enabled']&&value[key]===values[i]));
  $('humanStrength').value=level?level[0]:'custom';
  const scale=value.scale_enabled?value.scale:0;
  $('humanSummary').textContent=value.enabled?`仿真已启用 · 字大小 ${100-scale}%–${100+scale}% · 按各项勾选生成随机效果`:'仿真已关闭，保留原始字形排版。';
  updateProbabilities();
}
function humanizationChanged(){renderHumanization();invalidate();updatePresetHint();}
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
  if(metadata.writable)$('layoutSummary').textContent=metadata.layout_mode==='grid'?`可写范围 ${metadata.writable[0].toFixed(1)} × ${metadata.writable[1].toFixed(1)} mm · 格宽 ${metadata.grid.cell_width.toFixed(3)} / 格高 ${metadata.grid.cell_height.toFixed(3)} mm · ${metadata.rows} 行 × ${metadata.grid.columns} 列`:`可写范围 ${metadata.writable[0].toFixed(1)} × ${metadata.writable[1].toFixed(1)} mm · 每页 ${metadata.rows} 行 · 行距 ${metadata.pitch.toFixed(2)} mm`;

}
async function refreshCoordinatePreview(){
  if(!layoutMode)return;
  const requestRevision=++axisRevision, captured=revision;
  try{
    const guide=await api('/api/coordinate-preview',paperSettings());
    if(requestRevision!==axisRevision||captured!==revision)return;
    displayPaper(guide.svg,guide.metadata);
  }catch(error){if(requestRevision===axisRevision&&captured===revision)notice(error.message,true);}
}
function coordinateChanged(){invalidate();$('origin').checked=false;clearTimeout(axisTimer);axisTimer=setTimeout(refreshCoordinatePreview,150);}
function findPreset(){return [...builtinPresets,...layoutPresets].find(p=>p.id===selectedPreset);}
function validPresetValues(values,mode=layoutMode||'notebook'){
  if(!values||typeof values!=='object')return false;
  for(const key of layoutFields){
    const value=values[key], input=$(key);
    if(typeof value!=='number'||!Number.isFinite(value)||value<Number(input.min)||value>Number(input.max))return false;
  }
  if(!Number.isInteger(values.rows)||!Number.isInteger(values.gridColumns)||values.width<=values.marginLeft+values.marginRight)return false;
  const height=values.height-values.marginTop-values.marginBottom;
  return mode==='notebook'?height>=values.rows*values.size:
    Math.min((values.width-values.marginLeft-values.marginRight-(values.gridColumns-1)*values.gridColumnGap)/values.gridColumns,(height-(values.rows-1)*values.gridRowGap)/values.rows)>2*values.gridPadding+.2;
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
  const dirty=preset&&(layoutFields.some(key=>values[key]!==preset.values[key])||$('gridDrawLines').checked!==Boolean(preset.drawLines)||!sameHumanization(currentHumanization(),preset.humanization||humanDefault));
  $('presetHint').textContent=(dirty?'参数已修改，尚未保存。':'当前参数已载入预设。')+
    `保存${layoutMode==='grid'?'表格':'笔记'}模式的排版、标点、速度与仿真参数；仅保存在当前浏览器。`;
}
function storePresets(presets=layoutPresets, selected=selectedPreset){
  localStorage.setItem(presetStorage,JSON.stringify({presets,selected}));
}
function applyPreset(preset){
  selectedPreset=preset.id;
  for(const key of layoutFields)$(key).value=preset.values[key];
  setHumanization(preset.humanization||humanDefault);
  $('gridDrawLines').checked=Boolean(preset.drawLines);
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';
  renderPresets();coordinateChanged();
}
function restorePresets(){
  try{
    const saved=JSON.parse(localStorage.getItem(presetStorage));
    if(saved&&Array.isArray(saved.presets)){
      const ids=new Set(builtinPresets.map(p=>p.id)), names=new Set();
      layoutPresets=saved.presets.slice(0,50).filter(p=>{
        if(p&&p.values&&typeof p.values==='object')p.values={...defaultLayout,...p.values};
        if(!p||typeof p.id!=='string'||!p.id||p.id.length>100||ids.has(p.id)||typeof p.name!=='string'||!p.name.trim()||p.name.length>40||names.has(p.name)||!validPresetValues(p.values)||(p.humanization!==undefined&&!validHumanization(p.humanization)))return false;
        ids.add(p.id);names.add(p.name);return true;
      });
      if([...builtinPresets,...layoutPresets].some(p=>p.id===saved.selected))selectedPreset=saved.selected;
    }
  }catch(error){/* Invalid or unavailable browser storage leaves built-in presets usable. */}
  const preset=findPreset();
  for(const key of layoutFields)$(key).value=preset.values[key];
  setHumanization(preset.humanization||humanDefault);
  $('gridDrawLines').checked=Boolean(preset.drawLines);
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';renderPresets();
}
async function saveLayoutPreset(update=false){
  const name=$('presetName').value.trim(), values=currentLayout(), humanization=currentHumanization(), capturedPreset=selectedPreset, drawLines=$('gridDrawLines').checked, capturedMode=layoutMode;
  if(!name||name.length>40)throw new Error('请输入 1–40 字的预设名称');
  if(!layoutFields.every(key=>$(key).value!==''&&$(key).checkValidity())||!validPresetValues(values))throw new Error('排版参数无效，请检查宽高、边距、行数和字大小');
  if(!validHumanization(humanization)||Object.values(humanFields).some(suffix=>$('human'+suffix).value===''))throw new Error('仿真参数无效，请检查各项幅度');
  if(layoutPresets.some(p=>p.name===name&&(!update||p.id!==selectedPreset)))throw new Error('已有同名预设，请选择它后点击“更新当前预设”');
  if(update&&!layoutPresets.some(p=>p.id===selectedPreset))throw new Error('内置预设请另存为新预设');
  if(!update&&layoutPresets.length>=50)throw new Error('最多保存 50 个预设，请先删除不用的预设');
  await api('/api/coordinate-preview',paperSettings());
  // Capture values before validation, and refuse to save if an edit raced it.
  if(layoutMode!==capturedMode||$('gridDrawLines').checked!==drawLines||selectedPreset!==capturedPreset||layoutFields.some(key=>Number($(key).value)!==values[key])||!sameHumanization(currentHumanization(),humanization)||$('presetName').value.trim()!==name)throw new Error('保存期间参数已变化，请重新保存');
  const preset={id:update?selectedPreset:'preset-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,values,humanization,drawLines};
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
  $('preview').textContent=selectedFonts.length>1||$('humanEnabled').checked?'生成全部页 / 重新随机 ↗':'生成全部页面预览 ↗';
  updateFontPresetHint();
}
function validFontCombination(mix){
  return Array.isArray(mix)&&mix.length>=1&&mix.length<=16&&new Set(mix.map(f=>f&&f.font_id)).size===mix.length&&
    mix.every(f=>f&&typeof f.font_id==='string'&&f.font_id.length>0&&f.font_id.length<=200&&
      typeof f.weight==='number'&&Number.isFinite(f.weight)&&f.weight>=0&&f.weight<=1000000)&&mix.some(f=>f.weight>0);
}
function missingPresetFonts(preset){return preset.mix.filter(f=>!fonts.some(font=>font.id===f.font_id));}
function updateFontPresetHint(){
  const preset=fontPresets.find(p=>p.id===selectedFontPreset);
  const dirty=preset&&(preset.mix.length!==selectedFonts.length||preset.mix.some((f,i)=>f.font_id!==selectedFonts[i]?.font_id||f.weight!==selectedFonts[i]?.weight));
  $('fontPresetHint').textContent=(preset?(dirty?'组合已修改，尚未保存。':'当前字体组合已载入预设。'):'当前组合未保存。')+
    '保存所选字体及各自权重；仅保存在当前浏览器。';
}
function renderFontPresets(){
  $('fontPreset').replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='当前组合（未保存）';$('fontPreset').append(empty);
  for(const preset of fontPresets){
    const option=document.createElement('option');option.value=preset.id;option.textContent=preset.name+(missingPresetFonts(preset).length?'（缺少字体）':'');$('fontPreset').append(option);
  }
  $('fontPreset').value=selectedFontPreset;
  $('updateFontPreset').disabled=!selectedFontPreset;$('deleteFontPreset').disabled=!selectedFontPreset;
  $('undoFontPreset').hidden=!deletedFontPreset;updateFontPresetHint();
}
function storeFontPresets(presets=fontPresets,selected=selectedFontPreset){
  localStorage.setItem(fontPresetStorage,JSON.stringify({presets,selected}));
}
function applyFontPreset(preset){
  const missing=missingPresetFonts(preset);
  if(missing.length){
    $('fontPreset').value=selectedFontPreset;
    notice('无法载入字体预设，缺少字体：'+missing.map(f=>f.name||f.font_id).join('、')+'。已保留当前组合。',true);return false;
  }
  selectedFontPreset=preset.id;selectedFonts=preset.mix.map(f=>({font_id:f.font_id,weight:f.weight}));
  $('fontPresetName').value=preset.name;invalidate();renderFontMix();renderFontPresets();return true;
}
function restoreFontPresets(){
  let selected='';
  try{
    const saved=JSON.parse(localStorage.getItem(fontPresetStorage));
    if(saved&&Array.isArray(saved.presets)){
      const ids=new Set(),names=new Set();
      fontPresets=saved.presets.slice(0,50).filter(p=>{
        if(!p||typeof p.id!=='string'||!p.id||p.id.length>100||ids.has(p.id)||typeof p.name!=='string'||!p.name.trim()||p.name.length>40||names.has(p.name)||!validFontCombination(p.mix))return false;
        ids.add(p.id);names.add(p.name);return true;
      });
      selected=saved.selected;
    }
  }catch(error){/* Unavailable or invalid storage keeps the default font usable. */}
  fontPresetsLoaded=true;
  const preset=fontPresets.find(p=>p.id===selected);
  if(preset)applyFontPreset(preset);
  renderFontPresets();
}
function saveFontPreset(update=false){
  const name=$('fontPresetName').value.trim();
  if(!name||name.length>40)throw new Error('请输入 1–40 字的字体预设名称');
  if(!validFontCombination(selectedFonts)||selectedFonts.some(f=>!fonts.some(font=>font.id===f.font_id)))throw new Error('请选择 1–16 种可用字体并填写有效权重，至少一种权重大于 0');
  if(update&&!fontPresets.some(p=>p.id===selectedFontPreset))throw new Error('请先选择要更新的字体预设');
  if(fontPresets.some(p=>p.name===name&&(!update||p.id!==selectedFontPreset)))throw new Error('已有同名字体预设，请选择后点击“更新字体预设”');
  if(!update&&fontPresets.length>=50)throw new Error('最多保存 50 个字体预设，请先删除不用的预设');
  const preset={id:update?selectedFontPreset:'font-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,
    mix:selectedFonts.map(f=>({font_id:f.font_id,weight:f.weight,name:fonts.find(font=>font.id===f.font_id).name}))};
  const next=update?fontPresets.map(p=>p.id===selectedFontPreset?preset:p):[...fontPresets,preset];
  try{storeFontPresets(next,preset.id);}catch(error){throw new Error('浏览器不允许保存字体预设，请检查存储设置');}
  fontPresets=next;selectedFontPreset=preset.id;deletedFontPreset=null;renderFontPresets();notice(update?'字体预设已更新':'字体组合与权重已保存，刷新后仍可使用');
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
  if(!fontPresetsLoaded)restoreFontPresets();
  else renderFontPresets();
  renderFontMix();
  state=await api('/api/state');renderState();await loadPorts();
  await refreshCoordinatePreview();
}
async function action(task){if(busy)return;actionRevision++;busy=true;renderState();try{await task();}catch(error){notice(error.message,true);}finally{busy=false;renderState();}}
async function realtimeAction(task){actionRevision++;try{await task();}catch(error){notice(error.message,true);}finally{renderState();}}
function updateCount(){
  $('count').textContent=`本页 ${$('text').value.length} / 全文 ${sourcePages.reduce((n,p,i)=>n+(i===sourceIndex?$('text').value.length:p.length),0)} 字`;
}
function renderSourcePages(){
  $('sourcePage').replaceChildren();
  sourcePages.forEach((text,i)=>{const option=document.createElement('option');option.value=i;option.textContent=`预设第 ${i+1} 页`; $('sourcePage').append(option);});
  $('sourcePage').value=sourceIndex;$('text').value=sourcePages[sourceIndex];
  $('sourcePrev').disabled=sourceIndex===0;$('sourceNext').disabled=sourceIndex===sourcePages.length-1;
  $('removePage').disabled=sourcePages.length===1;$('addPage').disabled=sourcePages.length>=100;updateCount();
}
function selectSource(index){sourcePages[sourceIndex]=$('text').value;sourceIndex=index;renderSourcePages();}
function captureMode(){return {values:currentLayout(),humanization:currentHumanization(),drawLines:$('gridDrawLines').checked,selectedPreset};}
function setMode(mode){
  if(!['notebook','grid'].includes(mode))return;
  if(layoutMode)modeDrafts[layoutMode]=captureMode();
  layoutMode=mode;$('layoutMode').value=mode;$('compositionFields').disabled=false;
  builtinPresets=mode==='grid'?gridPresets:notebookPresets;
  presetStorage=mode==='grid'?'plotter.grid-presets.v1':'plotter.layout-presets.v1';
  layoutPresets=[];selectedPreset=builtinPresets[0].id;deletedPreset=null;restorePresets();
  const draft=modeDrafts[mode];
  if(draft){
    for(const key of layoutFields)$(key).value=draft.values[key];
    setHumanization(draft.humanization);$('gridDrawLines').checked=draft.drawLines;
    if([...builtinPresets,...layoutPresets].some(p=>p.id===draft.selectedPreset))selectedPreset=draft.selectedPreset;
    $('presetName').value=layoutPresets.find(p=>p.id===selectedPreset)?.name||'';renderPresets();
  }
  $('gridOptions').hidden=mode!=='grid';$('notebookOptions').hidden=mode!=='notebook';
  for(const key of ['size','spacing'])$(key).closest('label').hidden=mode==='grid';
  $('modeHint').textContent=mode==='grid'?'表格模式：普通字符逐格居中，公式占连续多格；预设与笔记模式独立。':'笔记模式：按宽度换行，支持中文标点留白与公式；预设与表格模式独立。';
  try{localStorage.setItem('plotter.layout-mode.v1',mode);}catch(error){/* Mode remains usable without storage. */}
  coordinateChanged();
}
function renderOutputNavigation(){
  const pages=documentResult?.pages||[];$('previewPage').replaceChildren();
  pages.forEach((page,i)=>{const option=document.createElement('option');option.value=i;option.textContent=`第 ${i+1} / ${pages.length} 页${page.metadata.empty?'（空白）':''}`;$('previewPage').append(option);});
  if(!pages.length){const option=document.createElement('option');option.textContent='尚未生成页面';$('previewPage').append(option);}
  $('previewPage').value=pages.length?outputIndex:0;$('previewPage').disabled=!pages.length;
  $('previewPrev').disabled=!pages.length||outputIndex===0;$('previewNext').disabled=!pages.length||outputIndex===pages.length-1;
  $('downloadAll').disabled=!pages.length||busy;
  $('documentSummary').textContent=pages.length?`共 ${pages.length} 张纸 · 来自 ${sourcePages.length} 个预设内容页 · 当前页来自预设第 ${pages[outputIndex].metadata.source_page} 页。每页需换纸并重新确认原点。`:layoutMode?'尚未生成全部页面。':'选择排版模式后开始编辑。';
}
function showOutputPage(index){
  if(!documentResult?.pages[index])return;
  outputIndex=index;const page=documentResult.pages[index],m=page.metadata;
  currentJob=m.empty||startedJobs.has(page.job_id)?null:page.job_id;downloadJob=page.job_id;$('origin').checked=false;
  axisRevision++;clearTimeout(axisTimer);displayPaper(page.svg,m);renderOutputNavigation();
  $('previewStatus').textContent=m.empty?'本页为空白页，可下载；不会发送书写任务。':`第 ${index+1} 页路径已生成${startedJobs.has(page.job_id)?'，已提交过该页任务':''}。下载与写入使用同一份路径。`;
  $('metrics').replaceChildren();
  for(const value of [`${m.lines.length} 行 · ${m.strokes} 笔画`,`${m.formula_count||0} 个公式 · ${m.commands} 条指令`,`${m.xy_mm} mm 路程`,`绘图 ${m.feed} / 移动 ${m.move_feed??3000} mm/min`]){const span=document.createElement('span');span.textContent=value;$('metrics').append(span);}
  renderFontUsage(m);renderState();
}
function saveBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('preview').onclick=()=>action(async()=>{
  if(!layoutMode)throw new Error('请先选择笔记或表格模式');
  if(!selectedFonts.length||!selectedFonts.some(f=>f.weight>0)||selectedFonts.some(f=>!Number.isFinite(f.weight)||f.weight<0||f.weight>1000000))throw new Error('请添加字体并设置有效权重，至少一种字体的权重须大于 0');
  sourcePages[sourceIndex]=$('text').value;
  const data={...paperSettings(),font_mix:selectedFonts.map(f=>({...f})),pages:sourcePages.map(text=>({text})),input_format:$('inputFormat').value};
  for(const key of ['spacing','feed'])data[key]=Number($(key).value);
  data.move_feed=Number($('moveFeed').value);
  const capturedRevision=revision;
  $('preview').disabled=true;
  try{
    const result=await api('/api/preview',data);
    if(capturedRevision!==revision){notice('生成期间内容发生了修改，请重新生成预览');return;}
    if(result.metadata.motion_revision!==undefined){state.motion_revision=result.metadata.motion_revision;observedMotionRevision=result.metadata.motion_revision;}
    documentResult=result;startedJobs.clear();showOutputPage(0);
    notice(`已生成 ${result.page_count} 页，可切换预览、逐页写字或下载全部页面`);
  }finally{$('preview').disabled=false;}
});
$('download').onclick=()=>action(async()=>{
  const response=await request(`/api/jobs/${downloadJob}/gcode`),blob=await response.blob(),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=`page-${String(outputIndex+1).padStart(3,'0')}.gcode`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('downloadAll').onclick=()=>action(async()=>{if(!documentResult)return;const response=await request(`/api/documents/${documentResult.document_id}/archive`);saveBlob(await response.blob(),'writing-pages.zip');});
$('previewPage').onchange=()=>showOutputPage(Number($('previewPage').value));
$('previewPrev').onclick=()=>showOutputPage(outputIndex-1);$('previewNext').onclick=()=>showOutputPage(outputIndex+1);
$('sourcePage').onchange=()=>selectSource(Number($('sourcePage').value));
$('sourcePrev').onclick=()=>selectSource(sourceIndex-1);$('sourceNext').onclick=()=>selectSource(sourceIndex+1);
$('addPage').onclick=()=>{sourcePages[sourceIndex]=$('text').value;sourcePages.splice(sourceIndex+1,0,'');sourceIndex++;renderSourcePages();invalidate();$('text').focus();};
$('removePage').onclick=()=>{if(sourcePages.length<=1)return;sourcePages.splice(sourceIndex,1);sourceIndex=Math.min(sourceIndex,sourcePages.length-1);renderSourcePages();invalidate();};
$('layoutMode').onchange=()=>setMode($('layoutMode').value);
$('pagination').onchange=invalidate;$('inputFormat').onchange=invalidate;
$('importText').onclick=()=>$('textFile').click();
$('textFile').onchange=()=>action(async()=>{
  const file=$('textFile').files[0];$('textFile').value='';if(!file)return;
  if(file.size>524288||! /\.(txt|md|markdown)$/i.test(file.name))throw new Error('请选择不超过 512 KiB 的 TXT / Markdown 文件');
  const text=(await file.text()).replace(/^\uFEFF/,'');sourcePages[sourceIndex]=$('text').value;
  if(sourcePages.reduce((n,p)=>n+p.length,0)+text.length>30000||sourcePages.length>=100)throw new Error('导入后超过 30000 字或 100 个预设页，请拆分文档');
  sourcePages.push(text);sourceIndex=sourcePages.length-1;$('inputFormat').value=/\.(md|markdown)$/i.test(file.name)?'markdown':'plain';
  renderSourcePages();invalidate();notice(`已将 ${file.name} 追加为预设第 ${sourceIndex+1} 页`);
});
$('insertFormula').onclick=()=>{const input=$('text');input.setRangeText('$x^2 + \\frac{1}{2} = \\sqrt{4}$',input.selectionStart,input.selectionEnd,'end');invalidate();input.focus();};
$('exportDocument').onclick=()=>{
  sourcePages[sourceIndex]=$('text').value;modeDrafts[layoutMode]=captureMode();
  const value={version:1,layout_mode:layoutMode,pages:sourcePages,modeDrafts,font_mix:selectedFonts,pagination:$('pagination').value,input_format:$('inputFormat').value,coordinate_system:coordinateSettings()};
  saveBlob(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),'writing-document.json');notice('文档已保存；载入后重新生成预览即可使用');
};
$('importDocument').onclick=()=>$('documentFile').click();
$('documentFile').onchange=()=>action(async()=>{
  const file=$('documentFile').files[0];$('documentFile').value='';if(!file)return;
  if(file.size>524288)throw new Error('文档文件不能超过 512 KiB');
  let value;try{value=JSON.parse(await file.text());}catch(error){throw new Error('无法读取文档 JSON');}
  if(value.version!==1||!['notebook','grid'].includes(value.layout_mode)||!Array.isArray(value.pages)||!value.pages.length||value.pages.length>100||value.pages.some(p=>typeof p!=='string')||value.pages.reduce((n,p)=>n+p.length,0)>30000||!['auto','fixed'].includes(value.pagination)||!['plain','markdown'].includes(value.input_format)||!validFontCombination(value.font_mix))throw new Error('文档格式或内容无效');
  if(!value.modeDrafts||typeof value.modeDrafts!=='object'||!value.modeDrafts[value.layout_mode])throw new Error('文档缺少当前模式的排版设置');
  for(const [mode,draft] of Object.entries(value.modeDrafts)){
    if(draft?.values&&typeof draft.values==='object'&&draft.values.moveFeed===undefined)draft.values.moveFeed=3000;
    if(!['notebook','grid'].includes(mode)||!draft||!validPresetValues(draft.values,mode)||!validHumanization(draft.humanization)||typeof draft.drawLines!=='boolean')throw new Error('文档排版设置无效');
  }
  const c=value.coordinate_system;
  if(!c||!['top-left','top-right','bottom-left','bottom-right'].includes(c.origin)||['reverse_x','reverse_y','swap_xy'].some(k=>typeof c[k]!=='boolean'))throw new Error('文档坐标设置无效');
  if(value.font_mix.some(f=>!fonts.some(font=>font.id===f.font_id)))throw new Error('文档使用的字体在当前服务中不可用，请先配置相同字体目录');
  sourcePages=[...value.pages];sourceIndex=0;renderSourcePages();selectedFonts=value.font_mix.map(f=>({...f}));renderFontMix();
  $('pagination').value=value.pagination;$('inputFormat').value=value.input_format;
  $('reverseX').checked=c.reverse_x;$('reverseY').checked=c.reverse_y;$('swapXY').checked=c.swap_xy;$('originCorner').value=c.origin;
  layoutMode='';modeDrafts=value.modeDrafts;setMode(value.layout_mode);notice('文档已载入，请重新生成全部页面预览');
});
$('refresh').onclick=()=>action(loadPorts);
$('authenticate').onclick=()=>action(load);
$('connect').onclick=()=>action(async()=>{state=await api('/api/connect',{port:$('port').value});notice('机器已连接');});
$('disconnect').onclick=()=>action(async()=>{state=await api('/api/disconnect',{});});
$('check').onclick=()=>action(async()=>{state=await api('/api/check',{job_id:currentJob});notice('原点与机器配置检查通过');});
$('start').onclick=()=>action(async()=>{const job=currentJob;state=await api('/api/start',{job_id:job,origin_confirmed:$('origin').checked});startedJobs.add(job);if(currentJob===job)currentJob=null;$('origin').checked=false;notice(state.mode==='simulation'?'模拟单页任务开始':'当前页写字任务开始；完成并换纸后再选择下一页');});
$('pause').onclick=()=>realtimeAction(async()=>{state=await api('/api/pause',{});});
$('resume').onclick=()=>realtimeAction(async()=>{state=await api('/api/resume',{});});
$('stop').onclick=()=>realtimeAction(async()=>{const returning=state.phase==='returning';state=await api('/api/stop',{});$('origin').checked=false;currentJob=null;notice(returning?'已请求停止回原点；请重新连接并确认位置':'已请求终止；停稳后先抬笔至 Z0，再返回本次 XY 原点');});
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
$('fontPreset').onchange=()=>{
  const preset=fontPresets.find(p=>p.id===$('fontPreset').value);
  if(preset){if(!applyFontPreset(preset))return;}
  else{selectedFontPreset='';$('fontPresetName').value='';renderFontPresets();}
  try{storeFontPresets();}catch(error){notice('组合已载入，但浏览器未能记住本次选择',true);}
};
$('saveFontPreset').onclick=()=>action(()=>saveFontPreset());
$('updateFontPreset').onclick=()=>action(()=>saveFontPreset(true));
$('deleteFontPreset').onclick=()=>{
  const preset=fontPresets.find(p=>p.id===selectedFontPreset);if(!preset)return;
  const next=fontPresets.filter(p=>p!==preset);
  try{storeFontPresets(next,'');}catch(error){notice('删除未保存，请检查浏览器存储设置',true);return;}
  deletedFontPreset={preset,index:fontPresets.indexOf(preset)};fontPresets=next;selectedFontPreset='';$('fontPresetName').value='';renderFontPresets();notice('字体预设已删除，当前组合保留，可撤销删除');
};
$('undoFontPreset').onclick=()=>{
  if(!deletedFontPreset)return;
  const preset=deletedFontPreset.preset,next=[...fontPresets];next.splice(deletedFontPreset.index,0,preset);
  // Restore the saved entry even if its fonts are temporarily unavailable.
  const usable=!missingPresetFonts(preset).length;
  try{storeFontPresets(next,usable?preset.id:selectedFontPreset);}catch(error){notice('恢复未保存，请检查浏览器存储设置',true);return;}
  fontPresets=next;deletedFontPreset=null;
  if(usable)applyFontPreset(preset);else renderFontPresets();notice('字体预设已恢复');
};
for(const key of ['text','spacing','feed','moveFeed'])$(key).addEventListener('input',invalidate);
for(const key of ['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size'])$(key).addEventListener('input',coordinateChanged);
for(const key of ['punctuationWidth','gridColumns','gridRowGap','gridColumnGap','gridSizePercent','gridPadding'])$(key).addEventListener('input',coordinateChanged);
$('gridDrawLines').onchange=()=>{coordinateChanged();updatePresetHint();};
for(const key of layoutFields)$(key).addEventListener('input',updatePresetHint);
$('humanEnabled').onchange=humanizationChanged;
for(const suffix of Object.values(humanFields)){
  $('human'+suffix).addEventListener('input',humanizationChanged);
  $('human'+suffix+'Enabled').addEventListener('change',humanizationChanged);
}
$('humanStrength').onchange=()=>{
  const values=humanLevels[$('humanStrength').value];if(!values)return;
  const value={...humanDefault,enabled:true};Object.keys(humanFields).forEach((key,i)=>value[key]=values[i]);
  setHumanization(value);humanizationChanged();
};
$('rerandomize').onclick=()=>{$('preview').click();};
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
  try{storePresets(next,builtinPresets[0].id);}catch(error){notice('删除未保存，请检查浏览器存储设置',true);return;}
  deletedPreset={preset,index:layoutPresets.indexOf(preset)};layoutPresets=next;applyPreset(builtinPresets[0]);notice('预设已删除，可点击“撤销删除”恢复');
};
$('undoPreset').onclick=()=>{
  if(!deletedPreset)return;
  const next=[...layoutPresets];next.splice(deletedPreset.index,0,deletedPreset.preset);
  try{storePresets(next,deletedPreset.preset.id);}catch(error){notice('恢复未保存，请检查浏览器存储设置',true);return;}
  const preset=deletedPreset.preset;layoutPresets=next;deletedPreset=null;applyPreset(preset);notice('预设已恢复');
};
async function poll(){
  try{if(!busy&&!speedSending){const capturedAction=actionRevision;const result=await api('/api/state');if(capturedAction===actionRevision&&!busy&&!speedSending){state=result;renderState();if(state.error&&state.error!==lastError){lastError=state.error;notice(state.error,true);}}}}
  catch(error){
    state={...state,connected:false,phase:'控制端离线，任务状态待确认',operation_type:null,status:'',position:null,pen:'unknown',pen_top:null};
    renderState();
    if(error.message!==lastError){lastError=error.message;notice('服务连接异常：'+error.message,true);}
  }
  finally{setTimeout(poll,800);}
}
renderSourcePages();renderOutputNavigation();
restoreCoordinateSettings();
restorePresets();
try{const savedMode=localStorage.getItem('plotter.layout-mode.v1');if(['notebook','grid'].includes(savedMode))setMode(savedMode);}catch(error){/* First visit asks the user to select a mode. */}
load().catch(error=>notice(error.message,true)).finally(poll);
