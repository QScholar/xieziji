'use strict';
// Browsers only edit/display the shared draft. SVG and G-code are generated
// once by the backend and restored by immutable document id on every client.
window.sharedWorkspace=(()=>{
  const fields=[...layoutFields,'pagination','inputFormat','reverseX','reverseY','swapXY','originCorner',
    'controllerType','penUpZ','penDownZ','penFeed','smartBalance','letterScale','digitScale',
    'lineSizing','lineLooseness','allowOverlap','gridDrawLines','humanEnabled','humanStrength',
    ...Object.values(humanFields).flatMap(s=>['human'+s,'human'+s+'Enabled'])];
  let device='',version=0,last='',ready=false,applying=false,pending=null,defaults=null,conflicted=false;
  const status=document.createElement('span');status.id='workspaceStatus';status.className='hint';
  const reset=document.createElement('button');reset.id='refreshInterface';reset.type='button';
  reset.className='text-button';reset.textContent='刷新界面';reset.title='清空当前设备的编辑内容和预览，所有访问端同步更新';
  const recover=document.createElement('button');recover.id='reloadSharedWorkspace';recover.type='button';
  recover.className='text-button';recover.textContent='载入共享内容';recover.hidden=true;
  const toolbar=document.createElement('div');toolbar.className='shared-workspace-toolbar';
  toolbar.append(status,recover,reset);document.querySelector('body > header').after(toolbar);
  function path(){return '/api/workspaces/'+encodeURIComponent(device);}
  function capture(){
    sourcePages[sourceIndex]=$('text').value;
    return JSON.parse(JSON.stringify({layoutMode,modeDrafts,selectedPreset,selectedFontPreset,pages:[...sourcePages],sourceIndex,outputIndex,
      font_mix:selectedFonts.map(f=>({...f})),previewValid:!!documentResult,previewStale:!!previewStale,
      fields:Object.fromEntries(fields.map(id=>[id,$(id).type==='checkbox'?$(id).checked:$(id).value]))}));
  }
  async function apply(workspace){
    applying=true;$('compose').inert=true;
    try{
      const draft=workspace.draft;
      revision++;axisRevision++;clearTimeout(axisTimer);clearTimeout(linePitchTimer);
      currentJob=null;downloadJob=null;documentResult=null;previewStale=false;$('origin').checked=false;
      layoutMode='';modeDrafts=draft.modeDrafts||{};
      if(draft.layoutMode)setMode(draft.layoutMode);
      else{
        $('layoutMode').value='';$('compositionFields').disabled=true;
        $('gridOptions').hidden=true;$('notebookOptions').hidden=true;sizingPanel.hidden=true;
      }
      for(const id of fields){if(!(id in draft.fields))continue;
        if($(id).type==='checkbox')$(id).checked=draft.fields[id];else $(id).value=draft.fields[id];}
      selectedPreset=draft.selectedPreset;selectedFontPreset=draft.selectedFontPreset;
      selectedFonts=draft.font_mix.map(f=>({...f}));renderFontMix();renderPresets();renderFontPresets();renderHumanization();
      $('letterScaleSlider').value=$('letterScale').value;$('digitScaleSlider').value=$('digitScale').value;
      sourcePages=[...draft.pages];sourceIndex=Math.min(draft.sourceIndex||0,sourcePages.length-1);renderSourcePages();
      outputIndex=0;$('paperView').replaceChildren();$('paperView').classList.remove('has-preview');
      $('metrics').replaceChildren();$('fontUsage').hidden=true;$('layoutSummary').textContent='';
      $('previewStatus').textContent='尚未生成预览';$('zoomPreview').disabled=true;
      clearTimeout(axisTimer);clearTimeout(linePitchTimer);
      if(draft.layoutMode)await refreshLinePitch();
      if(workspace.document_id&&draft.previewValid){
        const result=await api('/api/documents/'+workspace.document_id+'/preview');
        previewStale=!!draft.previewStale;
        documentResult=result;showOutputPage(Math.min(draft.outputIndex||0,result.pages.length-1));
      }else if(draft.layoutMode)await refreshCoordinatePreview();
      renderOutputNavigation();renderState();
      version=workspace.revision;last=JSON.stringify(capture());conflicted=false;recover.hidden=true;
      status.textContent='已同步';
    }finally{applying=false;$('compose').inert=false;}
  }
  async function commit(draft,extra={}){
    const result=await api(path(),{revision:version,draft,clear_preview:!draft.previewValid,...extra});
    if(result.conflict){
      conflicted=true;recover.hidden=false;status.textContent='其他页面已修改；本页修改尚未同步';
      throw new Error('其他页面已修改共享内容，本页修改已保留。请先保存文档备份，再点击“载入共享内容”。');
    }
    version=result.workspace.revision;last=JSON.stringify(draft);status.textContent='已同步';
    return result.workspace;
  }
  async function save(){
    if(pending)await pending;
    if(!ready||applying)return;
    if(conflicted)throw new Error('共享内容存在冲突，请先保存文档备份，再载入共享内容');
    const draft=capture();if(JSON.stringify(draft)===last)return;
    status.textContent='正在保存…';
    pending=commit(draft);
    try{await pending;}finally{pending=null;}
  }
  async function open(port){
    ready=false;device=port||'default';status.textContent='正在载入…';
    const initial=defaults?null:capture();
    if(!defaults){defaults=JSON.parse(JSON.stringify(initial));defaults.pages=[''];defaults.sourceIndex=0;defaults.outputIndex=0;defaults.layoutMode='';defaults.modeDrafts={};defaults.previewValid=false;}
    let workspace=await api(path());
    if(!workspace.draft){
      const draft=JSON.parse(JSON.stringify(initial||defaults));
      if(!initial){const current=capture();for(const id of ['controllerType','penUpZ','penDownZ','penFeed','reverseX','reverseY','swapXY','originCorner','feed','moveFeed'])draft.fields[id]=current.fields[id];}
      const result=await api(path(),{revision:workspace.revision,draft,clear_preview:true});
      workspace=result.workspace;
    }
    await apply(workspace);ready=true;
  }
  async function generate(render){
    await save();
    if(!ready)throw new Error('共享界面尚未载入');
    const draft=capture();draft.previewValid=true;draft.previewStale=false;draft.outputIndex=0;
    pending=commit(draft,{render});
    try{
      const workspace=await pending;
      return await api('/api/documents/'+workspace.document_id+'/preview');
    }finally{pending=null;}
  }
  reset.onclick=()=>action(async()=>{
    await save();const draft=capture();
    draft.pages=[''];draft.sourceIndex=0;draft.outputIndex=0;draft.layoutMode='';draft.modeDrafts={};draft.previewValid=false;draft.previewStale=false;
    const workspace=await commit(draft,{clear_preview:true});
    await apply(workspace);notice('当前设备的界面已重置，其他访问端将同步更新');
  });
  recover.onclick=()=>action(async()=>{if(pending)await pending;await apply(await api(path()));});
  // One serialized loop handles edits and remote changes; no load/reset or
  // random generation is triggered by browser reload, polling or visibility.
  async function tick(){
    try{
      if(ready&&!applying&&!busy&&!pending&&!conflicted){
        await save();
        const stamp=last,key=device,workspace=await api(path());
        if(!busy&&device===key&&stamp===JSON.stringify(capture())&&workspace.revision!==version)await apply(workspace);
      }
    }catch(error){status.textContent=conflicted?'内容冲突，等待处理':'未同步：'+error.message;}
    finally{setTimeout(tick,400);}
  }
  // Start saves immediately after input. keepalive also covers reload/closing
  // during the debounce window, without ever submitting a stale clean tab.
  let timer;
  function changed(){clearTimeout(timer);timer=setTimeout(()=>{if(!busy)save().catch(error=>{status.textContent='未同步：'+error.message;});},100);}
  document.addEventListener('input',changed);document.addEventListener('change',changed);document.addEventListener('click',changed);
  window.addEventListener('pagehide',()=>{
    if(!ready||applying||pending||conflicted)return;
    const draft=capture();if(JSON.stringify(draft)===last)return;
    const token=$('token').value.trim(),headers={'Content-Type':'application/json','X-Plotter-Controller':controllerId};
    if(token)headers.Authorization='Bearer '+token;
    fetch((window.PLOTTER_API_BASE||'').replace(/\/$/,'')+path(),{method:'POST',headers,keepalive:true,
      body:JSON.stringify({revision:version,draft,clear_preview:!draft.previewValid})}).catch(()=>{});
  });
  setTimeout(tick,400);
  return {save,open,generate,detach(){ready=false;device='';clearTimeout(timer);},get ready(){return ready;}};
})();
