'use strict';
const $ = id => document.getElementById(id);
const checkButton = document.createElement('button');
checkButton.id='check';checkButton.className='text-button';checkButton.textContent='检查原点与配置（只读）';
checkButton.style.marginTop='16px';document.querySelector('.connect-row').after(checkButton);
let fonts = [], currentJob = null, downloadJob = null, state = {connected:false,phase:'disconnected',total:0,acknowledged:0}, busy = false;
let lastError = '', noticeTimer, revision = 0, actionRevision = 0;
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
function invalidate(){revision++;currentJob=null;downloadJob=null;$('count').textContent=$('text').value.length+' 字';renderState();if($('paperView').querySelector('svg'))$('phase').textContent=active()?(phases[state.phase]+' · 编辑内容未执行'):'内容已修改，请重新生成预览';}
function filterFonts(){
  const selected=$('font').value, query=$('fontSearch').value.toLowerCase();
  $('font').replaceChildren();
  for(const font of fonts.filter(f=>f.name.toLowerCase().includes(query))) {
    const option=document.createElement('option');option.value=font.id;option.textContent=font.name;$('font').append(option);
  }
  if([...$('font').options].some(o=>o.value===selected))$('font').value=selected;
  if($('font').value!==selected)invalidate();
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
  state=await api('/api/state');renderState();await loadPorts();
}
async function action(task){if(busy)return;actionRevision++;busy=true;renderState();try{await task();}catch(error){notice(error.message,true);}finally{busy=false;renderState();}}
$('preview').onclick=()=>action(async()=>{
  const landscape=$('paper').value==='landscape';
  const data={font_id:$('font').value,text:$('text').value,width:landscape?297:210,height:landscape?210:297};
  for(const key of ['margin','size','pitch','spacing','feed'])data[key]=Number($(key).value);
  const capturedRevision=revision;
  $('preview').disabled=true;
  try{
    const result=await api('/api/preview',data);
    if(capturedRevision!==revision){notice('生成期间内容发生了修改，请重新生成预览');return;}
    currentJob=result.job_id;downloadJob=result.job_id;
    const parsed=new DOMParser().parseFromString(result.svg,'image/svg+xml');
    $('paperView').replaceChildren(document.importNode(parsed.documentElement,true));
    $('paperView').style.aspectRatio=data.width+'/'+data.height;
    $('paperLabel').textContent=`A4 / ${data.width} × ${data.height} mm`;
    const m=result.metadata;$('metrics').replaceChildren();
    for(const value of [`${m.lines.length} 行 · ${m.strokes} 笔画`,`${m.commands} 条指令`,`${m.xy_mm} mm 路程`]){const span=document.createElement('span');span.textContent=value;$('metrics').append(span);}
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
$('fontSearch').oninput=filterFonts;
for(const key of ['font','text','paper','margin','size','pitch','spacing','feed'])$(key).addEventListener('input',invalidate);
async function poll(){
  try{if(!busy){const capturedAction=actionRevision;const result=await api('/api/state');if(capturedAction===actionRevision&&!busy){state=result;renderState();if(state.error&&state.error!==lastError){lastError=state.error;notice(state.error,true);}}}}
  catch(error){if(error.message!==lastError){lastError=error.message;notice('服务连接异常：'+error.message,true);}}
  finally{setTimeout(poll,800);}
}
$('count').textContent=$('text').value.length+' 字';
load().catch(error=>notice(error.message,true)).finally(poll);
