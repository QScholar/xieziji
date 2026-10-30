'use strict';
const $ = id => document.getElementById(id);
const removeBlankLines=document.createElement('button');
removeBlankLines.id='removeBlankLines';removeBlankLines.type='button';removeBlankLines.className='text-button';
removeBlankLines.textContent='删除空白行';removeBlankLines.title='只清理当前预设页；保留正文缩进和显式分页符';
$('count').before(removeBlankLines);
$('allowOverlap').closest('label').hidden=true;
document.querySelector('.mix-heading span:last-child').textContent='适用范围 · 选择权重参考';
$('chooseFonts').addEventListener('click',()=>{$('fontDialog').showModal();$('fontSearch').focus();});
$('closeFontDialog').addEventListener('click',()=>{$('fontDialog').close();});

const sizingPanel=document.querySelector('.notebook-sizing');
$('width').closest('.grid').before(sizingPanel);sizingPanel.hidden=true;
const balancePanel=document.createElement('div');
balancePanel.className='font-balance';balancePanel.style.cssText='margin:12px 0;padding:12px;border:1px solid var(--border,#d8ded8);border-radius:8px';
balancePanel.innerHTML='<label class="inline-check"><input id="smartBalance" type="checkbox">智能均衡中英大小</label><div class="grid"><label>字母微调 / %<input id="letterScale" type="number" min="50" max="150" step="1" value="100"><input id="letterScaleSlider" aria-label="字母大小微调" type="range" min="50" max="150" step="1" value="100"></label><label>数字微调 / %<input id="digitScale" type="number" min="50" max="150" step="1" value="100"><input id="digitScaleSlider" aria-label="数字大小微调" type="range" min="50" max="150" step="1" value="100"></label></div>';
$('latinSize').closest('.grid').after(balancePanel);
const checkButton = document.createElement('button');
checkButton.id='check';checkButton.className='text-button';checkButton.textContent='检查原点与配置（只读）';
checkButton.style.marginTop='16px';document.querySelector('.connect-row').after(checkButton);
const controllerPanel=document.createElement('div');controllerPanel.className='controller-panel';
controllerPanel.innerHTML='<div id="controllerSwitch" class="controller-switch" role="group" aria-label="选择写字机"></div><p id="controllerTarget" class="hint" aria-live="polite"></p><section id="contactStartup" class="contact-startup" hidden><strong>使用前先断电换笔</strong><ol><li>无论上次如何结束，先断开写字机电源，再装笔，让笔尖接触纸面、笔架处于最低位。</li><li>重新通电，人工确认仍在最低位，再连接机器。</li><li>确认下方选项后，点击“上移 13.5 mm 并置零”；完成后顶部为 Z0、纸面为 Z13.5。</li></ol><p class="spring-warning">不要随意调整弹簧控制，也不要在校准后用手移动机构。换笔或调整后必须重新断电并重复以上流程。</p><label><input id="confirmPowerCycle" type="checkbox">已断电换笔并重新通电</label><label><input id="confirmPaperContact" type="checkbox">笔尖接触纸面，已人工确认笔架处于最低位</label><button id="prepareMachine" type="button" class="secondary" disabled>上移 13.5 mm 并置零</button><p id="preparationState" class="hint" aria-live="polite">尚未建立本次作业零点</p></section>';
document.querySelector('.machine-title').after(controllerPanel);
let controllerId='windows',controllerList=[],controllerGeneration=0;
function currentController(){return controllerList.find(c=>c.id===controllerId);}
function controllerName(){return currentController()?.name||'写字机';}
function contactStartup(){return currentController()?.contact_startup===true;}
function renderControllerSwitch(){
  const buttons=[...$('controllerSwitch').querySelectorAll('[data-controller]')];
  if(buttons.map(b=>b.dataset.controller).join(',')!==controllerList.map(c=>c.id).join(',')){
    $('controllerSwitch').replaceChildren();
    for(const item of controllerList){const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=item.name;button.dataset.controller=item.id;button.onclick=()=>switchController(item.id);$('controllerSwitch').append(button);}
  }
  for(const button of $('controllerSwitch').querySelectorAll('[data-controller]')){button.setAttribute('aria-pressed',String(button.dataset.controller===controllerId));button.disabled=busy||speedSending;}
  $('controllerTarget').textContent='当前操作：'+controllerName()+'。切换查看不会停止另一台设备的任务。';
  $('contactStartup').hidden=!contactStartup();
}
async function loadControllers(){
  const result=await api('/api/controllers');controllerList=result.controllers;
  let saved='';try{saved=localStorage.getItem('plotter.controller.v1')||'';}catch{}
  controllerId=controllerList.some(c=>c.id===saved)?saved:result.default;
  renderControllerSwitch();
}
async function switchController(next){
  if(next===controllerId||busy||speedSending)return;
  await action(async()=>{
    await window.sharedWorkspace?.save();if(profilePort)await saveDeviceProfile();
    window.sharedWorkspace?.detach();controllerGeneration++;actionRevision++;controllerId=next;
    try{localStorage.setItem('plotter.controller.v1',next);}catch{}
    clearTimeout(speedTimer);clearTimeout(axisTimer);clearTimeout(linePitchTimer);revision++;axisRevision++;speedPending=null;speedJob=null;observedMotionRevision=null;lastError='';
    clearTimeout(noticeTimer);$('notice').hidden=true;
    $('confirmPowerCycle').checked=false;$('confirmPaperContact').checked=false;$('origin').checked=false;
    profilePort='';profileLoaded=false;deviceProfiles=Object.create(null);currentJob=null;downloadJob=null;documentResult=null;previewStale=false;
    $('paperView').replaceChildren();$('metrics').replaceChildren();$('fontUsage').hidden=true;$('previewStatus').textContent='请选择设备配置并生成预览';
    state={connected:false,phase:'disconnected',total:0,acknowledged:0};renderControllerSwitch();renderOutputNavigation();renderState();
    try{state=await api('/api/state');}catch(error){notice(controllerName()+'：'+error.message,true);}
    await loadPorts();await window.sharedWorkspace?.open(profilePort);renderState();
  });
}
$('prepareMachine').onclick=()=>action(async()=>{
  if(!$('confirmPowerCycle').checked||!$('confirmPaperContact').checked)throw new Error('请先完成人工断电换笔和最低位确认');
  state=await api('/api/prepare',{power_cycled:true,pen_on_paper:true,bottom_confirmed:true});
  $('confirmPowerCycle').checked=false;$('confirmPaperContact').checked=false;$('origin').checked=false;
});
for(const id of ['confirmPowerCycle','confirmPaperContact'])$(id).addEventListener('change',()=>renderState());
let fonts = [], currentJob = null, downloadJob = null, state = {connected:false,phase:'disconnected',total:0,acknowledged:0}, busy = false;
let lastError = '', noticeTimer, revision = 0, actionRevision = 0;
let selectedFonts = [];
let deviceProfiles=Object.create(null), profilePort='', profileLoaded=false;
const defaultDeviceControl={controller_type:'kenjoy-grbl',pen_up:0,pen_down:10,pen_feed:600};
function deviceControl(){return contactStartup()?{controller_type:'grbl',pen_up:0,pen_down:13.5,pen_feed:100}:{controller_type:$('controllerType').value,pen_up:Number($('penUpZ').value),pen_down:Number($('penDownZ').value),pen_feed:Number($('penFeed').value)};}
function deviceLabel(port){return controllerName()+' · '+(deviceProfiles[port]?.name?deviceProfiles[port].name+' · '+port:port);}
function renderDeviceNames(){
  for(const option of $('port').options)option.textContent=deviceLabel(option.value)+(option.dataset.description?' · '+option.dataset.description:'');
  $('connection').textContent=state.connected?'已连接 · '+deviceLabel(state.port):'未连接机器';
  $('saveDeviceName').disabled=!profilePort||busy;
}
function captureDeviceProfile(port=profilePort){
  return {port,name:$('deviceName').value.trim(),device_control:deviceControl(),coordinate_system:coordinateSettings(),
    feed:Number($('feed').value),move_feed:Number($('moveFeed').value),jog_distance:Number($('jogDistance').value),jog_feed:Number($('jogFeed').value)};
}
async function saveDeviceProfile(){
  if(!profilePort)throw new Error('请先选择设备端口');
  const result=await api('/api/device-profiles',captureDeviceProfile());
  deviceProfiles[profilePort]=result.profile;renderDeviceNames();
}
function applyDeviceProfile(port){
  profilePort=port;
  const profile=deviceProfiles[port], control=contactStartup()?{controller_type:'grbl',pen_up:0,pen_down:13.5,pen_feed:100}:profile?.device_control||defaultDeviceControl;
  $('deviceName').value=profile?.name||'';
  $('controllerType').value=control.controller_type;
  $('penUpZ').value=control.pen_up;$('penDownZ').value=control.pen_down;$('penFeed').value=control.pen_feed;
  const coords=profile?.coordinate_system||{reverse_x:false,reverse_y:false,swap_xy:false,origin:'top-left'};
  $('reverseX').checked=coords.reverse_x;$('reverseY').checked=coords.reverse_y;$('swapXY').checked=coords.swap_xy;$('originCorner').value=coords.origin;
  $('feed').value=profile?.feed??10000;$('moveFeed').value=profile?.move_feed??10000;
  $('jogDistance').value=profile?.jog_distance??1;$('jogFeed').value=profile?.jog_feed??300;
  // Restoring settings never transmits a movement or reuses a previous task origin.
  coordinateChanged();renderDeviceNames();
}
$('port').addEventListener('change',()=>{
  const next=$('port').value, previous=profilePort;
  if(busy||state.connected){$('port').value=previous;return;}
  action(async()=>{
    try{await window.sharedWorkspace?.save();if(previous)await saveDeviceProfile();applyDeviceProfile(next);await window.sharedWorkspace?.open(next);notice('已切换设备档案与服务端保存的界面');}
    catch(error){$('port').value=previous;throw error;}
  });
});
$('saveDeviceName').addEventListener('click',()=>action(async()=>{await saveDeviceProfile();notice('设备档案已保存到服务端，控制参数在下次连接时载入');}));
$('deviceName').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();$('saveDeviceName').click();}});
let axisRevision = 0, axisTimer;
let observedMotionRevision = null;
let speedTimer, speedPending=null, speedSending=false, speedJob=null;
const layoutFields=['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size','latinSize','spacing','feed','moveFeed','punctuationWidth','gridColumns','gridRowGap','gridColumnGap','gridSizePercent','gridPadding'];
let presetStorage='plotter.layout-presets.v1';
let layoutMode='', modeDrafts={}, sourcePages=[$('text').value], sourceIndex=0, documentResult=null, outputIndex=0, previewStale=false;
const humanFields={vertical:'Vertical',scale:'Scale',rotation:'Rotation',spacing:'Spacing',baseline:'Baseline'};
const humanDefault={enabled:false,vertical:.3,scale:5,rotation:2,spacing:.2,baseline:.4,
  vertical_enabled:true,scale_enabled:true,rotation_enabled:true,spacing_enabled:true,baseline_enabled:true};
const humanLevels={light:[.15,3,1,.1,.2],medium:[.3,5,2,.2,.4],strong:[.6,10,4,.4,.8]};
const defaultLayout={width:210,height:297,marginTop:30,marginBottom:30,marginLeft:30,marginRight:30,rows:20,size:5,latinSize:5,spacing:.9,feed:10000,moveFeed:10000,punctuationWidth:.8,gridColumns:20,gridRowGap:2.9,gridColumnGap:0,gridSizePercent:80,gridPadding:.3};
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
  const headers = {'X-Plotter-Controller':controllerId},generation=controllerGeneration;
  const token = $('token').value.trim(); if(token) headers.Authorization='Bearer '+token;
  if(data!==undefined) headers['Content-Type']='application/json';
  const base=(window.PLOTTER_API_BASE||'').replace(/\/$/,'');
  const response = await fetch(base+path,{method:data===undefined?'GET':'POST',headers,body:data===undefined?undefined:JSON.stringify(data)});
  if(generation!==controllerGeneration)throw new Error('已切换设备，忽略旧设备响应');
  if(!response.ok) { const error=await response.json(); throw new Error(error.error || '请求失败'); }
  return response;
}
async function api(path,data) {const generation=controllerGeneration,result=await (await request(path,data)).json();if(generation!==controllerGeneration)throw new Error('已切换设备，忽略旧设备响应');return result;}
let presetBrowserStorage;
try{presetBrowserStorage=window.localStorage;}catch{presetBrowserStorage={getItem:()=>null,setItem:()=>{}};}
const serverPresets=new window.PresetStore(api,presetBrowserStorage);
let serverPresetUIReady=false;
function writing(){return state.operation_type==='writing'||['running','paused','draining','stopping','returning'].includes(state.phase);}
function active(){return writing()||['preparing','manual','manual_stopping'].includes(state.phase);}
function formatEtaDuration(seconds){
  const minutes=Math.max(1,Math.ceil(seconds/60));
  if(seconds<60)return '不足 1 分钟';
  return minutes>=60?`${Math.floor(minutes/60)} 小时${minutes%60?' '+minutes%60+' 分钟':''}`:`${minutes} 分钟`;
}
function renderEta(){
  const eta=state.eta||{},remaining=eta.remaining_seconds;
  let title='预计剩余 —',detail='';
  if(!state.connected&&state.job_id&&state.phase!=='disconnected'){title='ETA 暂不可用';detail='等待连接恢复';}
  else if(state.phase==='complete'){title='任务已完成';detail='';}
  else if(['stopping','returning','stopped','failed'].includes(state.phase)){detail='';}
  else if(state.phase==='paused'){
    title=`已暂停${Number.isFinite(remaining)?' · 剩余约 '+formatEtaDuration(remaining):''}`;
    detail='';
  }else if(eta.status==='draining'){
    title='机器收尾中';detail=Number.isFinite(remaining)?`预计还需 ${formatEtaDuration(remaining)}`:'等待机器确认';
  }else if(['running','draining'].includes(state.phase)){
    if(Number.isFinite(remaining)&&remaining>0){
      title='预计剩余约 '+formatEtaDuration(remaining);
      const finish=Number.isFinite(eta.estimated_finish_at)?new Date(eta.estimated_finish_at*1000):null;
      detail=finish?`预计完成 ${finish.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}`:'';
    }else{title='正在估算剩余时间…';detail='';}
  }
  $('taskEta').textContent=title;$('etaDetail').textContent=detail;$('etaDetail').hidden=!detail;
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
  $('liveSpeedStatus').textContent=state.speed_limit_update_pending?'速度上限待当前任务结束后更新':enabled?`已应用 ${state.feed_override}%${state.feed_override!==state.feed_override_requested?' · 目标 '+state.feed_override_requested+'%':''} · 上限 ${limit} mm/min`:writing()?'当前不可调速':'';
  $('liveSpeedStatus').hidden=!$('liveSpeedStatus').textContent;
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
  renderControllerSwitch();
  const prepared=!contactStartup()||state.calibrated===true;
  if(contactStartup()){$('controllerType').value='grbl';$('penUpZ').value=0;$('penDownZ').value=13.5;$('penFeed').value=100;}
  $('prepareMachine').disabled=!contactStartup()||!state.connected||prepared||active()||busy||!$('confirmPowerCycle').checked||!$('confirmPaperContact').checked||!(state.status||'').startsWith('<Idle');
  $('preparationState').textContent=state.phase==='preparing'?'正在低速上移 13.5 mm 并建立零点…':state.calibrated?'本次零点已建立 · 顶部 Z0 / 纸面 Z13.5':'尚未建立本次作业零点';
  renderSpeed();
  renderEta();
  window.pageReminders?.observe(state,{device:deviceLabel(state.port),simulation:state.mode==='simulation'});
  if(observedMotionRevision!==null&&state.motion_revision!==undefined&&observedMotionRevision!==state.motion_revision){
    $('origin').checked=false;
    $('previewStatus').textContent='笔位置已调整，预览保留；请确认当前位置作为本次原点。';
  }
  if(state.motion_revision!==undefined)observedMotionRevision=state.motion_revision;
  $('connection').textContent=controllerName()+' · '+(state.connected?'已连接 · '+deviceLabel(state.port):'未连接');
  $('connection').classList.toggle('online',state.connected);
  $('mode').textContent=state.mode==='simulation'?'模拟模式 · 不落笔':'C++ 控制服务';
  $('port').disabled=state.connected||busy||active();
  for(const key of ['controllerType','penUpZ','penDownZ','penFeed'])$(key).disabled=contactStartup()||state.connected||busy;
  $('saveDeviceName').disabled=busy||!profilePort;
  $('connect').disabled=state.connected||busy||state.speed_limit_update_pending===true; $('disconnect').disabled=!state.connected||active()||(!contactStartup()&&state.phase==='failed')||busy;
  $('check').disabled=!state.connected||active()||state.phase==='failed'||busy||state.speed_limit_update_pending===true;
  if(!prepared)$('check').disabled=true;
  $('start').disabled=!state.connected||!currentJob||!$('origin').checked||active()||busy||state.phase==='failed'||state.speed_limit_update_pending===true;
  if(!prepared)$('start').disabled=true;
  $('pause').disabled=!writing()||!['running','draining'].includes(state.phase);
  $('resume').disabled=state.phase!=='paused';
  $('stop').disabled=!writing()||state.phase==='stopping';
  $('stop').textContent=state.phase==='returning'?'停止回原点':'终止任务';
  const manual=['preparing','manual','manual_stopping'].includes(state.phase), controllerIdle=state.mode==='simulation'||(state.status||'').startsWith('<Idle');
  const writable=prepared&&state.connected&&controllerIdle&&!active()&&!busy&&!state.speed_limit_update_pending&&['idle','complete','stopped'].includes(state.phase);
  $('manualControls').disabled=writing();
  $('consoleLock').textContent=writing()?'写字任务中 · 已锁定':state.connected?'手动控制可用':'连接机器后可用';
  for(const key of ['penUp','penDown','jogDistance','jogFeed'])$(key).disabled=!writable;
  $('penUp').disabled=!writable||state.pen_top!==false;
  $('penUp').textContent=state.pen_top===true?'已在顶部':'抬笔 / Z'+(state.device_control?.pen_up??deviceControl().pen_up);
  $('penUp').title=state.pen_top===true?'已到达配置的抬笔位置':'读取最新 Z 坐标后抬笔';
  for(const button of document.querySelectorAll('[data-jog-axis],[data-step]'))button.disabled=!writable;
  $('manualPause').disabled=!manual||state.phase==='manual_stopping';
  $('manualStop').disabled=!manual||state.phase==='manual_stopping';
  $('consolePosition').textContent=contactStartup()?(Number.isFinite(state.logical_z)?`Z ${state.logical_z.toFixed(3)} mm`:'Z 待校准'):state.position?`Z ${state.position[2].toFixed(3)} mm`:'Z —';
  $('penState').textContent=({up:'已抬笔',down:'已落笔',unknown:'笔位置待确认'})[state.pen||'unknown'];
  $('consoleLastAction').textContent=state.last_action||'';$('consoleLastAction').hidden=!state.last_action;
  $('phase').textContent=state.phase==='preparing'?'正在建立本次零点':state.phase==='stopped'&&state.returned_to_origin!==true?'回原点已取消 · 请重新连接并确认位置':phases[state.phase]||state.phase;
  $('progressText').textContent=`${state.acknowledged} / ${state.total}`;
  $('progressFill').style.width=(state.total?100*state.acknowledged/state.total:0)+'%';
  const actualFeed=(state.status||'').match(/\|FS:([\d.]+),/);
  $('machineStatus').textContent=state.status?`${(state.status.match(/^<([^|>]+)/)||[])[1]||'未知状态'}${actualFeed?' · 瞬时进给 '+actualFeed[1]+' mm/min':''} · 抬笔 Z${state.device_control?.pen_up??deviceControl().pen_up} / 落笔 Z${state.device_control?.pen_down??deviceControl().pen_down}`:`115200 baud · 抬笔 Z${deviceControl().pen_up} / 落笔 Z${deviceControl().pen_down}`;
  $('download').disabled=!downloadJob;
  $('downloadAll').disabled=!documentResult||busy;
  $('rerandomize').disabled=busy;
}
// Content edits (keepPreview) retain the last generated preview — SVG, page
// navigation and metrics — until 生成全部页面预览 regenerates it; only the write
// task is released so stale content can never be started. Parameter changes
// still drop the preview because the paper geometry itself is no longer valid.
function invalidate(keepPreview=false){
  sourcePages[sourceIndex]=$('text').value;revision++;scheduleLinePitch();$('origin').checked=false;currentJob=null;
  if(keepPreview&&documentResult){previewStale=true;$('previewStatus').textContent='内容已修改 · 旧预览已保留，点击“生成全部页面预览”后更新';}
  else{previewStale=false;downloadJob=null;documentResult=null;renderOutputNavigation();$('fontUsage').hidden=true;$('previewStatus').textContent='参数已修改，请重新生成预览。';}
  updateCount();renderState();if($('paperView').querySelector('svg'))$('phase').textContent=active()?(phases[state.phase]+' · 编辑内容未执行'):'内容已修改，请重新生成预览';
}
function coordinateSettings(){return {reverse_x:$('reverseX').checked,reverse_y:$('reverseY').checked,swap_xy:$('swapXY').checked,origin:$('originCorner').value};}
function paperSettings(){syncNotebookSizing();return {line_placement:'ink',smart_balance:$('smartBalance').checked,letter_scale:Number($('letterScale').value),digit_scale:Number($('digitScale').value),line_sizing:$('lineSizing').value,line_looseness:Number($('lineLooseness').value),allow_overlap:allowOverlap(),layout_mode:layoutMode||'notebook',pagination:$('pagination').value,width:Number($('width').value),height:Number($('height').value),margin_top:Number($('marginTop').value),margin_bottom:Number($('marginBottom').value),margin_left:Number($('marginLeft').value),margin_right:Number($('marginRight').value),rows:Number($('rows').value),size:Number($('size').value),latin_size:Number($('latinSize').value),punctuation_width:Number($('punctuationWidth').value),grid:{columns:Number($('gridColumns').value),row_gap:Number($('gridRowGap').value),column_gap:Number($('gridColumnGap').value),size_percent:Number($('gridSizePercent').value),padding:Number($('gridPadding').value),draw_lines:$('gridDrawLines').checked},coordinate_system:coordinateSettings(),device_port:profilePort,device_control:deviceControl(),humanization:currentHumanization()};}
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
  $('humanSummary').textContent=value.enabled?`仿真已启用 · 字高 ${100-scale}%–${100+scale}%`:'仿真已关闭';
  updateProbabilities();
}
function humanizationChanged(){renderHumanization();invalidate();updatePresetHint();}
function renderCoordinateInfo(metadata){
  const [xd,yd]=metadata.axis_directions;
  $('axisSummary').textContent=`本次原点：${metadata.origin_label} · X+ ${xd} · Y+ ${yd}`;
  const b=metadata.machine_bounds||metadata.machine_page_bounds;
  $('axisRange').textContent=`${metadata.machine_bounds?'路径':'纸张'}输出范围 / mm：X [${b[0]}, ${b[2]}] · Y [${b[1]}, ${b[3]}]`;
  $('originText').textContent=`已确认${metadata.origin_label}起点 · 抬笔 Z${deviceControl().pen_up}`;
}
function displayPaper(svg, metadata, preservePaths=false){
  const parsed=new DOMParser().parseFromString(svg,'image/svg+xml');
  const next=document.importNode(parsed.documentElement,true), previous=$('paperView').querySelector('svg');
  if(preservePaths&&previous&&previous.getAttribute('data-paper-width')===next.getAttribute('data-paper-width')&&previous.getAttribute('data-paper-height')===next.getAttribute('data-paper-height')){
    const writing=previous.querySelector('#writing-paths');if(writing)next.querySelector('#writing-paths').replaceWith(writing.cloneNode(true));
  }
  $('paperView').replaceChildren(next);
  $('paperView').classList.add('has-preview');
  $('zoomPreview').disabled=false;
  if($('previewZoom').open)zoomMount(true);
  $('paperView').style.aspectRatio=metadata.preview_size[0]+'/'+metadata.preview_size[1];
  sizePaperView();
  $('paperLabel').textContent=`范围 / ${metadata.paper[0]} × ${metadata.paper[1]} mm`;
  renderCoordinateInfo(metadata);
  if(metadata.writable)$('layoutSummary').textContent=metadata.layout_mode==='grid'?`可写范围 ${metadata.writable[0].toFixed(1)} × ${metadata.writable[1].toFixed(1)} mm · 格宽 ${metadata.grid.cell_width.toFixed(3)} / 格高 ${metadata.grid.cell_height.toFixed(3)} mm · ${metadata.rows} 行 × ${metadata.grid.columns} 列`:`可写范围 ${metadata.writable[0].toFixed(1)} × ${metadata.writable[1].toFixed(1)} mm · ${metadata.rows} 基准行${metadata.line_layout?` · 本页 ${metadata.line_layout.length} 行`:''} · 基准行距 ${metadata.pitch.toFixed(2)} mm · 中文字高 ${metadata.size.toFixed(2)} / 英文 ${(metadata.latin_size??metadata.size).toFixed(2)} mm`;

}
// The paper grows with the window: bound by the column width in CSS, and by
// the viewport height here via the paper's own aspect ratio, so an A4 page
// stays fully visible while using as much of the screen as possible.
function sizePaperView(){
  const svg=$('paperView').querySelector('svg');if(!svg)return;
  const box=svg.viewBox?.baseVal,ratio=box&&box.width&&box.height?box.width/box.height:210/297;
  const height=Math.max(420,window.innerHeight-240);
  $('paperView').style.setProperty('--paper-max',Math.round(height*ratio)+'px');
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
// The zoom viewer reuses the SVG already on screen: no request, no new job, and
// the strokes shown are exactly the preview that download and writing use.
const zoomBox={scale:1,x:0,y:0,base:[0,0],drag:null,pointers:new Map(),pinch:null};
function zoomParts(){return {stage:$('zoomStage'),canvas:$('zoomCanvas')};}
function zoomSvg(){return $('paperView').querySelector('svg');}
function zoomRender(){
  const {canvas}=zoomParts();
  canvas.style.width=zoomBox.base[0]+'px';canvas.style.height=zoomBox.base[1]+'px';
  canvas.style.transform=`translate(${zoomBox.x}px, ${zoomBox.y}px) scale(${zoomBox.scale})`;
  $('zoomLevel').textContent=Math.round(zoomBox.scale*100)+'%';
  $('zoomFit').disabled=zoomBox.scale<=1.001;
}
function zoomLayout(keepView){
  const {stage,canvas}=zoomParts(), svg=canvas.querySelector('svg');
  if(!svg||!stage.clientWidth||!stage.clientHeight)return;
  const viewBox=svg.viewBox?.baseVal;
  const ratio=viewBox&&viewBox.width&&viewBox.height?viewBox.width/viewBox.height:210/297;
  const pad=30, room=[Math.max(60,stage.clientWidth-pad*2),Math.max(60,stage.clientHeight-pad*2)];
  const base=room[0]/room[1]>ratio?[room[1]*ratio,room[1]]:[room[0],room[0]/ratio];
  const previous=zoomBox.base;
  if(keepView&&previous[0]){
    // Keep the middle of the visible area over the same part of the page.
    const cx=stage.clientWidth/2, cy=stage.clientHeight/2;
    const fx=(cx-zoomBox.x)/zoomBox.scale/previous[0], fy=(cy-zoomBox.y)/zoomBox.scale/previous[1];
    zoomBox.x=cx-fx*zoomBox.scale*base[0]; zoomBox.y=cy-fy*zoomBox.scale*base[1];
  }else{
    zoomBox.scale=1;
    zoomBox.x=(stage.clientWidth-base[0])/2; zoomBox.y=(stage.clientHeight-base[1])/2;
  }
  zoomBox.base=base;zoomRender();
}
function zoomAt(clientX,clientY,factor){
  const {stage}=zoomParts(),rect=stage.getBoundingClientRect();
  const scale=Math.min(16,Math.max(.25,zoomBox.scale*factor)),grow=scale/zoomBox.scale;
  if(grow===1)return;
  const cx=clientX-rect.left, cy=clientY-rect.top;
  zoomBox.x=cx-(cx-zoomBox.x)*grow; zoomBox.y=cy-(cy-zoomBox.y)*grow;
  zoomBox.scale=scale;zoomRender();
}
function zoomMount(relayout){
  // The dialog shows a copy of the on-screen SVG, so a redraw while it is open
  // (coordinate or page change) updates the magnified view too.
  const source=zoomSvg(); if(!source)return false;
  const clone=source.cloneNode(true),canvas=zoomParts().canvas;
  clone.removeAttribute('style');clone.removeAttribute('width');clone.removeAttribute('height');
  canvas.replaceChildren(clone);
  if(relayout)zoomLayout(true);
  return true;
}
function previewZoomOpen(){
  const dialog=$('previewZoom');
  if(!zoomMount(false))return;
  if(!dialog.open)dialog.showModal();
  requestAnimationFrame(()=>{zoomLayout(false);$('zoomStage').focus({preventScroll:true});});
}
$('zoomPreview').onclick=previewZoomOpen;
$('previewZoom').addEventListener('close',()=>{$('zoomPreview').focus({preventScroll:true});});
$('closePreviewZoom').onclick=()=>$('previewZoom').close();
$('zoomFit').onclick=()=>zoomLayout(false);
$('zoomIn').onclick=()=>{const {stage}=zoomParts(),r=stage.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,1.35);};
$('zoomOut').onclick=()=>{const {stage}=zoomParts(),r=stage.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,1/1.35);};
$('zoomStage').addEventListener('wheel',event=>{
  event.preventDefault();
  zoomAt(event.clientX,event.clientY,Math.exp(-event.deltaY*0.0015));
},{passive:false});
$('zoomStage').addEventListener('dblclick',event=>{
  if(zoomBox.scale>1.05)zoomLayout(false); else zoomAt(event.clientX,event.clientY,2.4);
});
function zoomPinchPoints(){
  const points=[...zoomBox.pointers.values()];
  if(points.length<2)return null;
  const [a,b]=points;
  return {distance:Math.hypot(a.x-b.x,a.y-b.y),cx:(a.x+b.x)/2,cy:(a.y+b.y)/2};
}
$('zoomStage').addEventListener('pointerdown',event=>{
  if(event.pointerType==='mouse'&&event.button!==0)return;
  const {stage}=zoomParts();
  zoomBox.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  // Capture can be refused for a pointer that already ended; panning still works.
  try{stage.setPointerCapture(event.pointerId);}catch(error){}
  if(zoomBox.pointers.size===1){
    zoomBox.drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:zoomBox.x,top:zoomBox.y};
    stage.classList.add('is-dragging');
  }else if(zoomBox.pointers.size===2){
    // A second finger switches from panning to pinch zooming.
    zoomBox.drag=null;zoomBox.pinch=zoomPinchPoints();
  }
});
$('zoomStage').addEventListener('pointermove',event=>{
  if(!zoomBox.pointers.has(event.pointerId))return;
  zoomBox.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(zoomBox.pointers.size>=2){
    const next=zoomPinchPoints();
    if(!next)return;
    if(zoomBox.pinch&&zoomBox.pinch.distance>0)zoomAt(next.cx,next.cy,next.distance/zoomBox.pinch.distance);
    zoomBox.pinch=next;return;
  }
  const drag=zoomBox.drag;if(!drag||drag.id!==event.pointerId)return;
  zoomBox.x=drag.left+event.clientX-drag.x;zoomBox.y=drag.top+event.clientY-drag.y;zoomRender();
});
const endZoomDrag=event=>{
  const {stage}=zoomParts();
  zoomBox.pointers.delete(event.pointerId);
  if(zoomBox.pointers.size<2)zoomBox.pinch=null;
  // A finger left after a pinch becomes the new pan origin.
  const [id,point]=zoomBox.pointers.entries().next().value||[];
  zoomBox.drag=point?{id,x:point.x,y:point.y,left:zoomBox.x,top:zoomBox.y}:null;
  if(!zoomBox.pointers.size)stage.classList.remove('is-dragging');
};
$('zoomStage').addEventListener('pointerup',endZoomDrag);
$('zoomStage').addEventListener('pointercancel',endZoomDrag);
$('zoomStage').addEventListener('keydown',event=>{
  const step=event.shiftKey?120:40;
  const pans={ArrowLeft:[step,0],ArrowRight:[-step,0],ArrowUp:[0,step],ArrowDown:[0,-step]};
  if(pans[event.key]){zoomBox.x+=pans[event.key][0];zoomBox.y+=pans[event.key][1];zoomRender();event.preventDefault();}
  else if(event.key==='+'||event.key==='='||event.key==='-'){const {stage}=zoomParts(),r=stage.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,event.key==='-'?1/1.35:1.35);event.preventDefault();}
  else if(event.key==='0'){zoomLayout(false);event.preventDefault();}
});
window.addEventListener('resize',()=>{if($('previewZoom').open)zoomLayout(true);sizePaperView();});
$('paperView').addEventListener('click',()=>{if(zoomSvg())previewZoomOpen();});
function allowOverlap(){return false;}
function notebookSizing(){return {lineSizing:$('lineSizing').value,lineLooseness:Number($('lineLooseness').value),allowOverlap:$('allowOverlap').checked,smartBalance:$('smartBalance').checked,letterScale:Number($('letterScale').value),digitScale:Number($('digitScale').value)};}
function validNotebookSizing(value){return (value.lineSizing===undefined||['rows','size'].includes(value.lineSizing))&&(value.lineLooseness===undefined||(typeof value.lineLooseness==='number'&&Number.isFinite(value.lineLooseness)&&value.lineLooseness>=1&&value.lineLooseness<=4))&&(value.allowOverlap===undefined||typeof value.allowOverlap==='boolean')&&(value.smartBalance===undefined||typeof value.smartBalance==='boolean')&&['letterScale','digitScale'].every(key=>value[key]===undefined||(typeof value[key]==='number'&&Number.isFinite(value[key])&&value[key]>=50&&value[key]<=150));}
// `ratio` is the measured floor (for the hint); `min` is the slider limit, which
// overlap mode drops to 1.
let linePitchFloor={ratio:1,min:1,pitch:null,ink:null,feasible:true,error:''},linePitchTimer,linePitchRevision=0;
function restoreNotebookSizing(value){
  // A floor predicted for the previous preset must not rewrite this one; the
  // refresh below re-derives it for the restored layout.
  linePitchFloor={ratio:1,min:1,pitch:null,ink:null,feasible:true,error:''};
  $('lineSizing').value=value.lineSizing||'rows';
  $('allowOverlap').checked=Boolean(value.allowOverlap);
  $('smartBalance').checked=Boolean(value.smartBalance);
  for(const key of ['letterScale','digitScale']){$(key).value=value[key]??100;$(key+'Slider').value=$(key).value;}
  // A range input clamps on assignment, so clear the previous preset's floor
  // before restoring a value that only this preset's floor allows.
  $('lineLooseness').min='1';
  const v=value.values||currentLayout(),ratio=(v.height-v.marginTop-v.marginBottom)/v.rows/Math.max(v.size,v.latinSize||v.size);
  $('lineLooseness').value=value.lineLooseness??Math.max(1,Math.min(4,Math.round(ratio*100)/100));
  syncNotebookSizing();
}
function linePitchSettings(){
  // The floor depends on which glyphs actually appear, so send the same pages a
  // preview would receive; the editor's current text wins over the cached copy.
  const pages=sourcePages.map((text,index)=>({text:index===sourceIndex?$('text').value:text}));
  return {...paperSettings(),input_format:$('inputFormat').value,font_mix:selectedFonts.map(f=>({...f})),pages};
}
async function refreshLinePitch(){
  const request=++linePitchRevision;
  if(layoutMode!=='notebook'||!selectedFonts.some(f=>f.weight>0)){
    linePitchFloor={ratio:1,min:1,pitch:null,ink:null,feasible:true,error:''};syncNotebookSizing();return;
  }
  try{
    const result=await api('/api/line-pitch',linePitchSettings());
    if(request!==linePitchRevision)return;
    linePitchFloor={ratio:Math.min(4,Math.max(1,result.ratio)),min:Math.min(4,Math.max(1,result.min_ratio)),pitch:result.pitch_mm,ink:result.ink_mm,feasible:result.feasible,error:''};
  }catch(error){
    if(request!==linePitchRevision)return;
    linePitchFloor={ratio:1,min:1,pitch:null,ink:null,feasible:false,error:error.message};
  }
  syncNotebookSizing();
}
function scheduleLinePitch(){clearTimeout(linePitchTimer);linePitchTimer=setTimeout(refreshLinePitch,200);}
function syncNotebookSizing(){
  for(const key of ['letterScale','digitScale']){$(key).disabled=!$('smartBalance').checked;$(key+'Slider').disabled=!$('smartBalance').checked;}
  const notebook=layoutMode!=='grid',mode=$('lineSizing').value,overlap=allowOverlap();
  // The slider selects the nominal pitch; actual ink collisions are resolved locally.
  const floor=1;
  $('lineLooseness').min=floor.toFixed(2);
  if(Number($('lineLooseness').value)<floor)$('lineLooseness').value=floor.toFixed(2);
  $('rows').readOnly=notebook&&mode==='size';$('size').readOnly=notebook&&mode==='rows';$('latinSize').readOnly=$('size').readOnly;
  $('rows').closest('label').firstChild.textContent=$('rows').readOnly?'每页行数（自动）':'每页行数';
  $('size').closest('label').firstChild.textContent=$('size').readOnly?'中文字高 / mm（自动）':'中文字高 / mm';
  $('latinSize').closest('label').firstChild.textContent=$('latinSize').readOnly?'英文字高 / mm（自动）':'英文字高 / mm';
  $('rows').title=$('rows').readOnly?'根据字高和松紧自动计算':'';
  $('size').title=$('size').readOnly?'根据行数和松紧自动计算':'';
  $('latinSize').title=$('size').readOnly?'跟随中文字高按当前比例自动缩放':'';
  if(!notebook)return;
  const ratio=Number($('lineLooseness').value);
  $('lineLoosenessValue').textContent=ratio.toFixed(2)+' 倍行距';
  try{
    const result=window.NotebookSizing.compute(currentLayout(),mode,ratio,overlap);
    if(mode==='rows'){$('size').value=Number(result.size.toFixed(6));$('latinSize').value=Number(result.latinSize.toFixed(6));}else $('rows').value=result.rows;
    $('lineSizingHint').textContent=`${result.rows} 行 · 行距 ${result.pitch.toFixed(2)} mm`;
  }catch(error){$('lineSizingHint').textContent=error.message;}
}
function coordinateChanged(){syncNotebookSizing();scheduleLinePitch();invalidate();$('origin').checked=false;clearTimeout(axisTimer);axisTimer=setTimeout(refreshCoordinatePreview,150);}
function findPreset(){return [...builtinPresets,...layoutPresets].find(p=>p.id===selectedPreset);}
function validPresetValues(values,mode=layoutMode||'notebook'){
  if(!values||typeof values!=='object')return false;
  for(const key of layoutFields){
    const value=values[key], input=$(key);
    if(typeof value!=='number'||!Number.isFinite(value)||value<Number(input.min)||value>Number(input.max))return false;
  }
  if(!Number.isInteger(values.rows)||!Number.isInteger(values.gridColumns)||values.width<=values.marginLeft+values.marginRight)return false;
  const height=values.height-values.marginTop-values.marginBottom;
  return mode==='notebook'?height+.001>=values.rows*Math.max(values.size,values.latinSize):
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
  const oldRatio=preset?(preset.values.height-preset.values.marginTop-preset.values.marginBottom)/preset.values.rows/Math.max(preset.values.size,preset.values.latinSize):2.37;
  const savedRatio=preset?.lineLooseness??Math.max(1,Math.min(4,Math.round(oldRatio*100)/100));
  const dirty=preset&&(Boolean(preset.smartBalance)!==$('smartBalance').checked||(preset.letterScale??100)!==Number($('letterScale').value)||(preset.digitScale??100)!==Number($('digitScale').value)||layoutFields.some(key=>values[key]!==preset.values[key])||(layoutMode!=='grid'&&((preset.lineSizing||'rows')!==$('lineSizing').value||savedRatio!==Number($('lineLooseness').value)||Boolean(preset.allowOverlap)!==$('allowOverlap').checked))||$('gridDrawLines').checked!==Boolean(preset.drawLines)||!sameHumanization(currentHumanization(),preset.humanization||humanDefault));
  $('presetHint').textContent=dirty?'有未保存的修改':'';
  $('presetHint').hidden=!dirty;
}
async function storePresets(presets=layoutPresets, selected=selectedPreset){
  await serverPresets.save(presetStorage,presets,selected);
}
function applyPreset(preset){
  selectedPreset=preset.id;
  for(const key of layoutFields)$(key).value=preset.values[key];
  restoreNotebookSizing(preset);
  setHumanization(preset.humanization||humanDefault);
  $('gridDrawLines').checked=Boolean(preset.drawLines);
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';
  renderPresets();coordinateChanged();
}
function restorePresets(){
  try{
    const saved=serverPresets.read(presetStorage);
    if(saved&&Array.isArray(saved.presets)){
      const ids=new Set(builtinPresets.map(p=>p.id)), names=new Set();
      layoutPresets=saved.presets.filter(p=>{
        if(p&&p.values&&typeof p.values==='object')p.values={...defaultLayout,...p.values,latinSize:p.values.latinSize??p.values.size??defaultLayout.latinSize};
        if(!p||typeof p.id!=='string'||!p.id||p.id.length>100||ids.has(p.id)||typeof p.name!=='string'||!p.name.trim()||p.name.length>40||names.has(p.name)||!validPresetValues(p.values)||!validNotebookSizing(p)||(p.humanization!==undefined&&!validHumanization(p.humanization)))return false;
        ids.add(p.id);names.add(p.name);return true;
      });
      if([...builtinPresets,...layoutPresets].some(p=>p.id===saved.selected))selectedPreset=saved.selected;
    }
  }catch(error){/* Invalid or unavailable browser storage leaves built-in presets usable. */}
  const preset=findPreset();
  for(const key of layoutFields)$(key).value=preset.values[key];
  restoreNotebookSizing(preset);
  setHumanization(preset.humanization||humanDefault);
  $('gridDrawLines').checked=Boolean(preset.drawLines);
  $('presetName').value=layoutPresets.includes(preset)?preset.name:'';renderPresets();
}
async function saveLayoutPreset(update=false){
  const sizing=notebookSizing();
  const name=$('presetName').value.trim(), values=currentLayout(), humanization=currentHumanization(), capturedPreset=selectedPreset, drawLines=$('gridDrawLines').checked, capturedMode=layoutMode;
  if(!name||name.length>40)throw new Error('请输入 1–40 字的预设名称');
  if(!layoutFields.every(key=>$(key).value!==''&&$(key).checkValidity())||!validPresetValues(values))throw new Error('排版参数无效，请检查宽高、边距、行数和字大小');
  if(!validHumanization(humanization)||Object.values(humanFields).some(suffix=>$('human'+suffix).value===''))throw new Error('仿真参数无效，请检查各项幅度');
  if(layoutPresets.some(p=>p.name===name&&(!update||p.id!==selectedPreset)))throw new Error('已有同名预设，请选择它后点击“更新当前预设”');
  if(update&&!layoutPresets.some(p=>p.id===selectedPreset))throw new Error('内置预设请另存为新预设');
  if(!update&&layoutPresets.length>=500)throw new Error('最多保存 500 个预设，请先删除不用的预设');
  await api('/api/coordinate-preview',paperSettings());
  // Capture values before validation, and refuse to save if an edit raced it.
  if(layoutMode!==capturedMode||$('gridDrawLines').checked!==drawLines||selectedPreset!==capturedPreset||layoutFields.some(key=>Number($(key).value)!==values[key])||!sameHumanization(currentHumanization(),humanization)||$('presetName').value.trim()!==name)throw new Error('保存期间参数已变化，请重新保存');
  if(JSON.stringify(sizing)!==JSON.stringify(notebookSizing()))throw new Error('保存期间松紧或排版方式已变化，请重新保存');
  const preset={id:update?selectedPreset:'preset-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,values,humanization,drawLines,...sizing};
  const next=update?layoutPresets.map(p=>p.id===selectedPreset?preset:p):[...layoutPresets,preset];
  await storePresets(next,preset.id);
  layoutPresets=next;selectedPreset=preset.id;deletedPreset=null;renderPresets();notice(update?'当前预设已更新到服务器':'新预设已保存到服务器');
}
function restoreCoordinateSettings(){
  try{
    const saved=JSON.parse(localStorage.getItem('plotter.coordinates.v1'));
    if(!saved||!['top-left','top-right','bottom-left','bottom-right'].includes(saved.origin)||['reverse_x','reverse_y','swap_xy'].some(key=>typeof saved[key]!=='boolean'))return;
    $('reverseX').checked=saved.reverse_x;$('reverseY').checked=saved.reverse_y;$('swapXY').checked=saved.swap_xy;$('originCorner').value=saved.origin;
  }catch(error){/* Browser storage may be unavailable. Default coordinates remain usable. */}
}
const fontScopes=['all','zh','en'];
const scopeLabels={all:'中英通用',zh:'仅中文',en:'仅英文'};
function currentScope(font){return fontScopes.includes(font.scope)?font.scope:'all';}
// Weights compare only within a script: a Chinese-only font never competes for Latin text.
function fontScripts(font){
  const scope=currentScope(font);
  return scope==='all'?['zh','en']:[scope];
}
function scopeProbabilities(){
  const totals={zh:0,en:0};
  for(const font of selectedFonts)for(const script of fontScripts(font))
    totals[script]+=Number.isFinite(font.weight)?font.weight:0;
  return selectedFonts.map(font=>Object.fromEntries(fontScripts(font)
    .map(script=>[script,totals[script]>0?font.weight/totals[script]:0])));
}
function probabilityText(shares){
  return fontScopes.filter(scope=>scope!=='all'&&shares[scope]!==undefined)
    .map(scope=>(scope==='zh'?'中 ':'英 ')+(100*shares[scope]).toFixed(0)+'%').join(' · ');
}
function updateProbabilities(){
  const shares=scopeProbabilities();
  for(const [index,node] of [...$('fontMix').querySelectorAll('.font-probability')].entries())node.textContent=probabilityText(shares[index]||{})||'—';
  $('addFont').disabled=!$('font').value||selectedFonts.length>=16||selectedFonts.some(f=>f.font_id===$('font').value);
  $('preview').textContent=selectedFonts.length>1||$('humanEnabled').checked?'生成全部页 / 重新随机 ↗':'生成全部页面预览 ↗';
  updateFontPresetHint();
}
function validFontCombination(mix){
  return Array.isArray(mix)&&mix.length>=1&&mix.length<=16&&new Set(mix.map(f=>f&&f.font_id)).size===mix.length&&
    mix.every(f=>f&&typeof f.font_id==='string'&&f.font_id.length>0&&f.font_id.length<=200&&
      typeof f.weight==='number'&&Number.isFinite(f.weight)&&f.weight>=0&&f.weight<=1000000&&
      (f.scope===undefined||fontScopes.includes(f.scope)))&&mix.some(f=>f.weight>0);
}
function missingPresetFonts(preset){return preset.mix.filter(f=>!fonts.some(font=>font.id===f.font_id));}
function updateFontPresetHint(){
  const preset=fontPresets.find(p=>p.id===selectedFontPreset);
  const dirty=preset&&(preset.mix.length!==selectedFonts.length||preset.mix.some((f,i)=>f.font_id!==selectedFonts[i]?.font_id||f.weight!==selectedFonts[i]?.weight||(f.scope||'all')!==currentScope(selectedFonts[i]||{})));
  $('fontPresetHint').textContent=dirty?'组合有未保存的修改':'';
  $('fontPresetHint').hidden=!dirty;
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
async function storeFontPresets(presets=fontPresets,selected=selectedFontPreset){
  await serverPresets.save(fontPresetStorage,presets,selected);
}
function applyFontPreset(preset){
  const missing=missingPresetFonts(preset);
  if(missing.length){
    $('fontPreset').value=selectedFontPreset;
    notice('无法载入字体预设，缺少字体：'+missing.map(f=>f.name||f.font_id).join('、')+'。已保留当前组合。',true);return false;
  }
  selectedFontPreset=preset.id;selectedFonts=preset.mix.map(f=>({font_id:f.font_id,weight:f.weight,scope:currentScope(f)}));
  $('fontPresetName').value=preset.name;invalidate();renderFontMix();renderFontPresets();return true;
}
function restoreFontPresets(){
  let selected='';
  try{
    const saved=serverPresets.read(fontPresetStorage);
    if(saved&&Array.isArray(saved.presets)){
      const ids=new Set(),names=new Set();
      fontPresets=saved.presets.filter(p=>{
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
async function saveFontPreset(update=false){
  const name=$('fontPresetName').value.trim();
  if(!name||name.length>40)throw new Error('请输入 1–40 字的字体预设名称');
  if(!validFontCombination(selectedFonts)||selectedFonts.some(f=>!fonts.some(font=>font.id===f.font_id)))throw new Error('请选择 1–16 种可用字体并填写有效权重，至少一种权重大于 0');
  if(update&&!fontPresets.some(p=>p.id===selectedFontPreset))throw new Error('请先选择要更新的字体预设');
  if(fontPresets.some(p=>p.name===name&&(!update||p.id!==selectedFontPreset)))throw new Error('已有同名字体预设，请选择后点击“更新字体预设”');
  if(!update&&fontPresets.length>=500)throw new Error('最多保存 500 个字体预设，请先删除不用的预设');
  const preset={id:update?selectedFontPreset:'font-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,
    mix:selectedFonts.map(f=>({font_id:f.font_id,weight:f.weight,scope:currentScope(f),name:fonts.find(font=>font.id===f.font_id).name}))};
  const next=update?fontPresets.map(p=>p.id===selectedFontPreset?preset:p):[...fontPresets,preset];
  await storeFontPresets(next,preset.id);
  fontPresets=next;selectedFontPreset=preset.id;deletedFontPreset=null;renderFontPresets();notice(update?'字体预设已更新到服务器':'字体组合与权重已保存到服务器');
}
function renderFontMix(){
  $('fontMix').replaceChildren();
  for(const selected of selectedFonts){
    const font=fonts.find(f=>f.id===selected.font_id);
    const row=document.createElement('div');row.className='font-mix-row';
    const name=document.createElement('span');name.className='font-mix-name';name.textContent=font.name;name.title=font.name;
    const scope=document.createElement('select');scope.className='font-scope';scope.setAttribute('aria-label',font.name+' 适用范围');
    for(const value of fontScopes){const option=document.createElement('option');option.value=value;option.textContent=scopeLabels[value];scope.append(option);}
    scope.value=currentScope(selected);
    scope.onchange=()=>{selected.scope=scope.value;invalidate();updateProbabilities();};
    const weight=document.createElement('input');weight.type='number';weight.min='0';weight.max='1000000';weight.step='any';weight.value=selected.weight;weight.setAttribute('aria-label',font.name+' 权重');
    weight.oninput=()=>{selected.weight=weight.value===''?0:Number(weight.value);invalidate();updateProbabilities();};
    const probability=document.createElement('span');probability.className='font-probability';
    const remove=document.createElement('button');remove.className='text-button font-remove';remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除 '+font.name);
    remove.onclick=()=>{selectedFonts=selectedFonts.filter(f=>f!==selected);invalidate();renderFontMix();};
    const sample=document.createElement('div');sample.className='font-sample font-mix-sample';sample.dataset.fontPreview=font.id;sample.textContent='加载字形…';
    row.append(name,scope,weight,probability,remove,sample);$('fontMix').append(row);
  }
  if(!selectedFonts.length){const empty=document.createElement('p');empty.className='hint';empty.textContent='从上方添加至少一种字体。';$('fontMix').append(empty);}
  updateProbabilities();
  window.FontBrowser?.refreshSelected();
}
function renderFontUsage(metadata){
  $('fontUsage').replaceChildren();
  const title=document.createElement('p');title.className='font-usage-title';title.textContent='本次预览的实际字体分布';$('fontUsage').append(title);
  for(const item of metadata.font_mix||[]){
    const row=document.createElement('div');row.className='font-usage-row';
    const name=document.createElement('span');name.textContent=(item.external_fallback?'补字 · ':'')+(fonts.find(f=>f.id===item.font_id)?.name||item.font)+(item.scope&&item.scope!=='all'?`（${scopeLabels[item.scope]}）`:'');name.title=name.textContent;
    const count=document.createElement('span');count.textContent=`${item.used} 字 · ${(100*item.actual_probability).toFixed(1)}%`;const balance=metadata.font_balance?.fonts.find(f=>f.font_id===item.font_id);if(balance){const detail=document.createElement('small');detail.style.display='block';detail.textContent=`英数实际缩放：字母 ${(100*balance.letters.scale).toFixed(0)}% / 数字 ${(100*balance.digits.scale).toFixed(0)}%`;if(!balance.letters.samples||!balance.digits.samples)detail.textContent+='（部分样本不足，保留原比例）';name.append(detail);}row.append(name,count);$('fontUsage').append(row);
  }
  if(metadata.fallback_characters){const note=document.createElement('p');note.className='hint';note.textContent=`缺字替代 ${metadata.fallback_characters} 字 · 相近字体补字 ${metadata.external_fallback_characters||0} 字（橙色）`;$('fontUsage').append(note);}
  if(metadata.repeat_distribution?.groups.length){
    const details=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('ul');
    summary.textContent=`本页同字分散 · ${metadata.repeat_distribution.groups.length} 种重复字符`;
    for(const group of metadata.repeat_distribution.groups.slice(0,40)){
      const row=document.createElement('li');row.textContent=`「${group.character}」× ${group.occurrences} · 使用 ${group.used_fonts} / ${group.available_fonts} 种可用字体`+(group.nearest_same_font_mm===null?' · 字体未重复':` · 最近同字体距离 ${group.nearest_same_font_mm.toFixed(1)} mm`);list.append(row);
    }
    if(metadata.repeat_distribution.groups.length>40){const more=document.createElement('li');more.textContent='更多结果见页面 JSON。';list.append(more);}
    details.append(summary,list);$('fontUsage').append(details);
  }
  if(metadata.glyph_fallbacks?.length){
    const list=document.createElement('ul');list.className='fallback-details';
    const seen=new Set();for(const item of metadata.glyph_fallbacks){const key=JSON.stringify([item.character,item.reference_font_id,item.font_id]);if(seen.has(key))continue;seen.add(key);
      const row=document.createElement('li');row.textContent=`「${item.character}」 ${item.reference_font} → ${item.font} · 相似评分 ${item.score}`;list.append(row);}
    $('fontUsage').append(list);
  }
  $('fontUsage').hidden=false;
}
function filterFonts(){
  const selected=$('font').value;
  $('font').replaceChildren();
  for(const font of fonts) {
    const option=document.createElement('option');option.value=font.id;option.textContent=font.name;$('font').append(option);
  }
  if([...$('font').options].some(o=>o.value===selected))$('font').value=selected;
  updateProbabilities();
  window.FontBrowser?.refresh();
}
async function loadPorts(){
  const [result,saved]=await Promise.all([api('/api/ports').catch(error=>{notice(controllerName()+'：'+error.message,true);return {ports:[]};}),api('/api/device-profiles')]);
  deviceProfiles=saved.profiles;const old=state.connected?state.port:$('port').value;$('port').replaceChildren();
  for(const port of result.ports){const option=document.createElement('option');option.value=port.device;option.dataset.description=port.description||'';$('port').append(option);}
  if(result.ports.some(p=>p.device===old))$('port').value=old;
  else if(result.ports.some(p=>p.device==='COM5'))$('port').value='COM5';
  if(state.connected&&!result.ports.some(p=>p.device===state.port)){const option=document.createElement('option');option.value=state.port;$('port').append(option);$('port').value=state.port;}
  // The previous UI defaulted to COM5. Migrate its browser-wide saved axes
  // once; newly selected ports always start with their own settings.
  const legacyPort=state.connected?state.port:($('port').value==='COM5'?'COM5':'');
  let legacySaved=false;try{legacySaved=!!localStorage.getItem('plotter.coordinates.v1');}catch(error){}
  if(!profileLoaded&&legacyPort&&!deviceProfiles[legacyPort]&&(state.connected||legacySaved)){
    restoreCoordinateSettings();
    const draft=captureDeviceProfile(legacyPort);
    if(state.device_control)draft.device_control=state.device_control;
    deviceProfiles[legacyPort]=(await api('/api/device-profiles',draft)).profile;
  }
  if(!profileLoaded||profilePort!==$('port').value){
    const switchWorkspace=window.sharedWorkspace?.ready;
    if(switchWorkspace)await window.sharedWorkspace.save();
    applyDeviceProfile($('port').value);profileLoaded=true;
    if(switchWorkspace)await window.sharedWorkspace.open(profilePort);
  }else renderDeviceNames();
}
async function load(){
  await serverPresets.init();
  if(!serverPresetUIReady){
    restorePresets();serverPresetUIReady=true;
    if(serverPresets.imported)notice(`已将本机 ${serverPresets.imported} 个预设迁移到服务器，本地备份已保留`);
  }
  const result=await api('/api/fonts');fonts=result.fonts;filterFonts();
  const defaultFont=fonts.find(f=>f.name==='blood8-2_kvenjoy_blood8');if(defaultFont)$('font').value=defaultFont.id;
  selectedFonts=selectedFonts.filter(f=>fonts.some(font=>font.id===f.font_id));
  if(!selectedFonts.length&&$('font').value)selectedFonts=[{font_id:$('font').value,weight:100,scope:'all'}];
  if(!fontPresetsLoaded)restoreFontPresets();
  else renderFontPresets();
  renderFontMix();
  window.FontBrowser?.start();
  state=await api('/api/state');renderState();await loadPorts();
  await refreshLinePitch();
  await refreshCoordinatePreview();
  await window.sharedWorkspace?.open(profilePort);
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
function captureMode(){return {values:currentLayout(),humanization:currentHumanization(),drawLines:$('gridDrawLines').checked,selectedPreset,...notebookSizing()};}
function setMode(mode){
  if(serverPresets.pending){$('layoutMode').value=layoutMode;return;}
  if(!['notebook','grid'].includes(mode))return;
  if(layoutMode)modeDrafts[layoutMode]=captureMode();
  layoutMode=mode;$('layoutMode').value=mode;$('compositionFields').disabled=false;
  builtinPresets=mode==='grid'?gridPresets:notebookPresets;
  presetStorage=mode==='grid'?'plotter.grid-presets.v1':'plotter.layout-presets.v1';
  layoutPresets=[];selectedPreset=builtinPresets[0].id;deletedPreset=null;restorePresets();
  const draft=modeDrafts[mode];
  if(draft){
    for(const key of layoutFields)$(key).value=draft.values[key];
    restoreNotebookSizing(draft);
    setHumanization(draft.humanization);$('gridDrawLines').checked=draft.drawLines;
    if([...builtinPresets,...layoutPresets].some(p=>p.id===draft.selectedPreset))selectedPreset=draft.selectedPreset;
    $('presetName').value=layoutPresets.find(p=>p.id===selectedPreset)?.name||'';renderPresets();
  }
  $('gridOptions').hidden=mode!=='grid';$('notebookOptions').hidden=mode!=='notebook';
  sizingPanel.hidden=mode!=='notebook';
  for(const key of ['size','latinSize','spacing'])$(key).closest('label').hidden=mode==='grid';
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
  $('documentSummary').textContent=pages.length?`共 ${pages.length} 页 · 当前来自预设第 ${pages[outputIndex].metadata.source_page} 页`:'';
  $('documentSummary').hidden=!pages.length;
}
function showOutputPage(index){
  if(!documentResult?.pages[index])return;
  outputIndex=index;const page=documentResult.pages[index],m=page.metadata;
  // Content stays writable: every start becomes a new execution of this page.
  // A stale preview stays browsable but never starts a write task.
  currentJob=previewStale||m.empty?null:page.job_id;downloadJob=page.job_id;$('origin').checked=false;
  axisRevision++;clearTimeout(axisTimer);displayPaper(page.svg,m);renderOutputNavigation();
  $('previewStatus').textContent=m.empty?'本页为空白页':previewStale?`旧预览 · 第 ${index+1} 页 · 内容已修改，生成后更新`:`第 ${index+1} 页已生成`;
  $('metrics').replaceChildren();
  for(const value of [`${m.lines.length} 行 · ${m.strokes} 笔画`,`${m.formula_count||0} 个公式 · ${m.commands} 条指令`,`${m.xy_mm} mm 路程`,`绘图 ${m.feed} / 移动 ${m.move_feed??10000} mm/min`]){const span=document.createElement('span');span.textContent=value;$('metrics').append(span);}
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
    const result=window.sharedWorkspace?await window.sharedWorkspace.generate(data):await api('/api/preview',data);
    if(capturedRevision!==revision){notice('生成期间内容发生了修改，请重新生成预览');return;}
    if(result.metadata.motion_revision!==undefined){state.motion_revision=result.metadata.motion_revision;observedMotionRevision=result.metadata.motion_revision;}
    previewStale=false;documentResult=result;showOutputPage(0);
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
$('addPage').onclick=()=>{sourcePages[sourceIndex]=$('text').value;sourcePages.splice(sourceIndex+1,0,'');sourceIndex++;renderSourcePages();invalidate(true);$('text').focus();};
$('removePage').onclick=()=>{if(sourcePages.length<=1)return;sourcePages.splice(sourceIndex,1);sourceIndex=Math.min(sourceIndex,sourcePages.length-1);renderSourcePages();invalidate(true);};
const textZoomDialog=$('textZoom');
function syncTextZoom(){
  const markdown=$('inputFormat').value==='markdown'&&$('textZoomPreview').checked;
  $('textZoomRender').hidden=!markdown;
  if(markdown&&window.renderMarkdown)$('textZoomRender').innerHTML=window.renderMarkdown($('textZoomArea').value);
  $('textZoomInfo').textContent=`预设第 ${sourceIndex+1} 页 · ${$('textZoomArea').value.length} 字 · 关闭时写回当前预设页`;
}
let textZoomTimer=0;
$('expandText').onclick=()=>{
  $('textZoomArea').value=$('text').value;
  $('textZoomPreviewToggle').hidden=$('inputFormat').value!=='markdown';
  syncTextZoom();
  textZoomDialog.showModal();
  $('textZoomArea').focus();
};
$('textZoomArea').addEventListener('input',()=>{clearTimeout(textZoomTimer);textZoomTimer=setTimeout(syncTextZoom,120);});
$('textZoomPreview').onchange=syncTextZoom;
$('closeTextZoom').onclick=()=>textZoomDialog.close();
textZoomDialog.addEventListener('close',()=>{
  const value=$('textZoomArea').value;
  if(value!==$('text').value){$('text').value=value;invalidate(true);}
  $('text').focus({preventScroll:true});
});
removeBlankLines.onclick=()=>{
  const text=$('text').value,lines=text.split(/\r\n?|\n/);
  const kept=lines.filter(line=>line.trim()!==''||line.includes('\f'));
  const removed=lines.length-kept.length;
  if(!text||!removed){notice('当前页没有可删除的空白行');return;}
  $('text').value=kept.join('\n');invalidate(true);
  notice(`已删除当前页 ${removed} 个空白行，请重新生成预览。`);
};
$('layoutMode').onchange=()=>setMode($('layoutMode').value);
$('pagination').onchange=invalidate;$('inputFormat').onchange=invalidate;
$('importText').onclick=()=>$('textFile').click();
$('textFile').onchange=()=>action(async()=>{
  const file=$('textFile').files[0];$('textFile').value='';if(!file)return;
  if(file.size>524288||! /\.(txt|md|markdown)$/i.test(file.name))throw new Error('请选择不超过 512 KiB 的 TXT / Markdown 文件');
  const text=(await file.text()).replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n');
  if(sourcePages.reduce((n,p,i)=>n+(i===sourceIndex?0:p.length),0)+text.length>30000)throw new Error('导入后超过 30000 字，请拆分文档');
  sourcePages[sourceIndex]=text;$('inputFormat').value=/\.(md|markdown)$/i.test(file.name)?'markdown':'plain';
  renderSourcePages();invalidate(true);notice(`已用 ${file.name} 替换预设第 ${sourceIndex+1} 页的内容`);
});
$('insertFormula').onclick=()=>{const input=$('text');input.setRangeText('$x^2 + \\frac{1}{2} = \\sqrt{4}$',input.selectionStart,input.selectionEnd,'end');invalidate(true);input.focus();};
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
    if(draft?.values&&typeof draft.values==='object'&&draft.values.moveFeed===undefined)draft.values.moveFeed=10000;
    if(!['notebook','grid'].includes(mode)||!draft||!validPresetValues(draft.values,mode)||!validNotebookSizing(draft)||!validHumanization(draft.humanization)||typeof draft.drawLines!=='boolean')throw new Error('文档排版设置无效');
  }
  const c=value.coordinate_system;
  if(!c||!['top-left','top-right','bottom-left','bottom-right'].includes(c.origin)||['reverse_x','reverse_y','swap_xy'].some(k=>typeof c[k]!=='boolean'))throw new Error('文档坐标设置无效');
  if(value.font_mix.some(f=>!fonts.some(font=>font.id===f.font_id)))throw new Error('文档使用的字体在当前服务中不可用，请先配置相同字体目录');
  sourcePages=[...value.pages];sourceIndex=0;renderSourcePages();selectedFonts=value.font_mix.map(f=>({...f,scope:currentScope(f)}));renderFontMix();
  $('pagination').value=value.pagination;$('inputFormat').value=value.input_format;
  $('reverseX').checked=c.reverse_x;$('reverseY').checked=c.reverse_y;$('swapXY').checked=c.swap_xy;$('originCorner').value=c.origin;
  layoutMode='';modeDrafts=value.modeDrafts;setMode(value.layout_mode);notice('文档已载入，请重新生成全部页面预览');
});
$('refresh').onclick=()=>action(loadPorts);
$('authenticate').onclick=()=>action(load);
$('connect').onclick=()=>action(async()=>{await saveDeviceProfile();state=await api('/api/connect',{port:profilePort});$('origin').checked=false;$('confirmPowerCycle').checked=false;$('confirmPaperContact').checked=false;notice(contactStartup()?'机器已连接，请确认断电放笔流程后建立本次零点':'机器已连接，已载入对应设备控制配置');});
$('disconnect').onclick=()=>action(async()=>{state=await api('/api/disconnect',{});$('confirmPowerCycle').checked=false;$('confirmPaperContact').checked=false;$('origin').checked=false;});
$('check').onclick=()=>action(async()=>{state=await api('/api/check',{job_id:currentJob});notice('原点与机器配置检查通过');});
$('start').onclick=()=>action(async()=>{const job=currentJob, page=documentResult?.pages.find(p=>p.job_id===job)?.metadata;state=await api('/api/start',{job_id:job,origin_confirmed:$('origin').checked});window.pageReminders?.remember(state,{device:deviceLabel(state.port),simulation:state.mode==='simulation',page:page?.page_number,pages:page?.page_count});$('origin').checked=false;notice(state.mode==='simulation'?'模拟单页任务开始':'当前页写字任务开始；完成后可换纸重新确认原点，再写同一页');});
$('pause').onclick=()=>realtimeAction(async()=>{state=await api('/api/pause',{});});
$('resume').onclick=()=>realtimeAction(async()=>{state=await api('/api/resume',{});});
$('stop').onclick=()=>realtimeAction(async()=>{const returning=state.phase==='returning';state=await api('/api/stop',{});$('origin').checked=false;notice(returning?'已请求停止回原点；请重新连接并确认位置':'已请求终止；停稳后先抬笔至 Z0，再返回本次 XY 原点');});
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
for(const key of ['reverseX','reverseY','swapXY','originCorner','controllerType','penUpZ','penDownZ','penFeed'])$(key).addEventListener('change',coordinateChanged);
$('saveCoordinates').onclick=()=>action(async()=>{await saveDeviceProfile();notice('坐标与控制设置已保存到当前设备档案');});
$('resetCoordinates').onclick=()=>{$('reverseX').checked=false;$('reverseY').checked=false;$('swapXY').checked=false;$('originCorner').value='top-left';coordinateChanged();};
$('addFont').onclick=()=>{
  if(!$('font').value||selectedFonts.length>=16||selectedFonts.some(f=>f.font_id===$('font').value))return;
  selectedFonts.push({font_id:$('font').value,weight:100,scope:$('fontScope').value});invalidate();renderFontMix();
};
$('font').onchange=()=>{updateProbabilities();window.FontBrowser?.focus();};
$('fontSearch').oninput=filterFonts;
$('fontPreset').onchange=()=>{
  if(busy){$('fontPreset').value=selectedFontPreset;return;}
  const preset=fontPresets.find(p=>p.id===$('fontPreset').value);
  if(preset){if(!applyFontPreset(preset))return;}
  else{selectedFontPreset='';$('fontPresetName').value='';renderFontPresets();}
  serverPresets.selection(fontPresetStorage,selectedFontPreset);
};
$('saveFontPreset').onclick=()=>action(()=>saveFontPreset());
$('updateFontPreset').onclick=()=>action(()=>saveFontPreset(true));
$('deleteFontPreset').onclick=()=>action(async()=>{
  const preset=fontPresets.find(p=>p.id===selectedFontPreset);if(!preset)return;
  const next=fontPresets.filter(p=>p!==preset);
  await storeFontPresets(next,'');
  deletedFontPreset={preset,index:fontPresets.indexOf(preset)};fontPresets=next;selectedFontPreset='';$('fontPresetName').value='';renderFontPresets();notice('字体预设已删除，当前组合保留，可撤销删除');
});
$('undoFontPreset').onclick=()=>action(async()=>{
  if(!deletedFontPreset)return;
  const preset=deletedFontPreset.preset,next=[...fontPresets];next.splice(deletedFontPreset.index,0,preset);
  // Restore the saved entry even if its fonts are temporarily unavailable.
  const usable=!missingPresetFonts(preset).length;
  await storeFontPresets(next,usable?preset.id:selectedFontPreset);
  fontPresets=next;deletedFontPreset=null;
  if(usable)applyFontPreset(preset);else renderFontPresets();notice('字体预设已恢复');
});
for(const key of ['spacing','feed','moveFeed'])$(key).addEventListener('input',invalidate);
$('text').addEventListener('input',()=>invalidate(true));
$('smartBalance').onchange=()=>{coordinateChanged();updatePresetHint();};
for(const key of ['letterScale','digitScale']){
  $(key).addEventListener('input',()=>{$(key+'Slider').value=$(key).value;coordinateChanged();updatePresetHint();});
  $(key+'Slider').addEventListener('input',()=>{$(key).value=$(key+'Slider').value;coordinateChanged();updatePresetHint();});
}
$('lineSizing').onchange=()=>{coordinateChanged();updatePresetHint();};
$('lineLooseness').addEventListener('input',()=>{coordinateChanged();updatePresetHint();});
$('allowOverlap').onchange=()=>{coordinateChanged();scheduleLinePitch();updatePresetHint();};
for(const key of ['width','height','marginTop','marginBottom','marginLeft','marginRight','rows','size','latinSize'])$(key).addEventListener('input',coordinateChanged);
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
  if(busy){$('layoutPreset').value=selectedPreset;return;}
  const preset=[...builtinPresets,...layoutPresets].find(p=>p.id===$('layoutPreset').value);
  if(!preset)return;
  applyPreset(preset);
  serverPresets.selection(presetStorage,selectedPreset);
};
$('savePreset').onclick=()=>action(()=>saveLayoutPreset());
$('updatePreset').onclick=()=>action(()=>saveLayoutPreset(true));
$('deletePreset').onclick=()=>action(async()=>{
  const preset=layoutPresets.find(p=>p.id===selectedPreset);if(!preset)return;
  const next=layoutPresets.filter(p=>p!==preset);
  await storePresets(next,builtinPresets[0].id);
  deletedPreset={preset,index:layoutPresets.indexOf(preset)};layoutPresets=next;applyPreset(builtinPresets[0]);notice('预设已删除，可点击“撤销删除”恢复');
});
$('undoPreset').onclick=()=>action(async()=>{
  if(!deletedPreset)return;
  const next=[...layoutPresets];next.splice(deletedPreset.index,0,deletedPreset.preset);
  await storePresets(next,deletedPreset.preset.id);
  const preset=deletedPreset.preset;layoutPresets=next;deletedPreset=null;applyPreset(preset);notice('预设已恢复');
});
async function poll(){
  const polledController=controllerId;
  try{if(!busy&&!speedSending){const capturedAction=actionRevision;const result=await api('/api/state');if(capturedAction===actionRevision&&!busy&&!speedSending){state=result;renderState();if(state.error&&state.error!==lastError){lastError=state.error;notice(state.error,true);}}}}
  catch(error){
    if(polledController!==controllerId)return;
    state={...state,connected:false,phase:'控制端离线，任务状态待确认',operation_type:null,status:'',position:null,pen:'unknown',pen_top:null};
    renderState();
    if(error.message!==lastError){lastError=error.message;notice('服务连接异常：'+error.message,true);}
  }
  finally{setTimeout(poll,800);}
}
window.pageReminders=new window.PageNotifications({hint:$('pageNotificationHint'),toggle:$('enablePageNotifications'),test:$('testPageNotification'),completion:$('pageCompletion')});
renderSourcePages();renderOutputNavigation();
restorePresets();
try{const savedMode=localStorage.getItem('plotter.layout-mode.v1');if(['notebook','grid'].includes(savedMode))setMode(savedMode);}catch(error){/* First visit asks the user to select a mode. */}
loadControllers().then(load).catch(error=>notice(error.message,true)).finally(poll);
