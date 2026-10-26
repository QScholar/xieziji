'use strict';
// Browsing fonts only requests vector samples; it never submits a writing job.
window.FontBrowser=(()=>{
  const byId=id=>document.getElementById(id);
  const cache=new Map(),pending=new Set();
  let page=0,paintTimer,inputTimer,catalog=null,loaded=false,sampleRevision=0,catalogRevision=0;
  const pageSize=6;
  const previewPanel=byId('fontPreviewPanel'),mobile=matchMedia('(max-width:760px)');
  previewPanel.open=!mobile.matches;
  mobile.addEventListener('change',()=>{previewPanel.open=!mobile.matches;});
  previewPanel.querySelector('summary').addEventListener('click',event=>{if(!mobile.matches)event.preventDefault();});
  const refreshButton=document.createElement('button');refreshButton.id='refreshFontLibrary';refreshButton.type='button';refreshButton.className='secondary';refreshButton.textContent='刷新字库';
  const refreshStatus=document.createElement('p');refreshStatus.id='fontRefreshStatus';refreshStatus.className='hint';refreshStatus.setAttribute('role','status');refreshStatus.setAttribute('aria-live','polite');
  const refreshTools=document.createElement('div');refreshTools.className='font-refresh-tools';refreshTools.append(refreshButton,refreshStatus);
  byId('fontRefreshHost').append(refreshTools);
  let checking=false,checkingRequest=false,lastChecked=null,lastAnalyzed=null;
  function showStatus(status){
    checking=status.running;refreshButton.disabled=checking;
    refreshButton.textContent=checking?(status.phase==='analyzing'?'正在分析…':'正在检查…'):'刷新字库';
    const checked=status.checked_at?new Date(status.checked_at*1000).toLocaleString():'';
    refreshStatus.textContent=status.error?'检查或分析失败：'+status.error:
      checking?(status.phase==='analyzing'?'字库有更新，正在计算速度评分和相似度；完成后自动更新。':'正在检查字体文件…'):
      checked?`已检查：${checked} · 服务端每天自动检查一次`:'服务端每天自动检查一次，可手动刷新。';
    if(status.invalid_fonts?.length)refreshStatus.textContent+=` · ${status.invalid_fonts.length} 个无效字体文件已跳过`;
  }
  async function syncFonts(){
    const result=await api('/api/fonts');fonts=result.fonts;
    const retained=selectedFonts.filter(item=>fonts.some(font=>font.id===item.font_id));
    if(retained.length!==selectedFonts.length){
      selectedFonts=retained;selectedFontPreset='';invalidate();renderFontMix();renderFontPresets();
      notice('部分已选字体已从字库移除，请确认字体组合后重新生成预览。',true);
    }
    sampleRevision++;cache.clear();filterFonts();loaded=false;await loadRecommendations();
  }
  async function checkStatus(){
    if(checkingRequest)return;checkingRequest=true;
    try{
      const status=await api('/api/font-status');showStatus(status);
      if(status.checked_at!==lastChecked||status.analyzed_at!==lastAnalyzed){
        await syncFonts();lastChecked=status.checked_at;lastAnalyzed=status.analyzed_at;
      }
    }catch(error){refreshStatus.textContent='字库状态读取失败：'+error.message;}
    finally{checkingRequest=false;}
  }
  refreshButton.onclick=async()=>{
    refreshButton.disabled=true;
    try{showStatus(await api('/api/font-refresh',{}));await checkStatus();}
    catch(error){refreshStatus.textContent='刷新失败：'+error.message;refreshButton.disabled=false;}
  };
  byId('chooseFonts').addEventListener('click',checkStatus);
  setInterval(()=>{if(checking||byId('fontDialog').open)checkStatus();},3000);
  const sort=byId('fontSort'),speedHint=byId('fontSpeedHint');
  sort.onchange=()=>gallery(true);
  function speedNode(id){
    const node=document.createElement('p');node.className='font-speed';
    const speed=catalog?.fonts?.[id]?.speed;
    if(!speed||speed.score==null){node.textContent=catalog?'速度评分暂无数据':'速度评分加载中…';return node;}
    node.dataset.speedScore=speed.score;
    node.textContent=`速度评分 ${speed.score.toFixed(1)}${speed.confidence==='limited'?' · 样本较少':''}`;
    node.title=`越高越快；100 为字库中位水平。${speed.sample_characters} 个样本字，平均 ${speed.strokes_per_character} 段/字，落笔 ${speed.draw_mm_per_character} mm/字，空移 ${speed.move_mm_per_character} mm/字；模型估计。`;
    const detail=document.createElement('span');detail.className='font-speed-detail';
    detail.textContent=`${speed.strokes_per_character} 段/字 · 落笔 ${speed.draw_mm_per_character} mm/字 · ${speed.sample_characters} 样本字`;
    node.append(detail);return node;
  }
  function sampleText(){return byId('fontSampleText').value.trim();}
  function singleChar(){return Array.from(byId('fontSingleChar').value.trim())[0]||'永';}
  function sampleNode(id,text=null){const node=document.createElement('div');node.className='font-sample';node.dataset.fontPreview=id;if(text!==null){node.dataset.previewText=text;node.classList.add('font-thumbnail');}node.textContent='加载字形…';return node;}
  function renderSample(node,value){
    node.replaceChildren();
    node.dataset.sampleText=value.text;
    if(value.error){node.textContent='预览暂不可用：'+value.error;return;}
    const doc=new DOMParser().parseFromString(value.svg,'image/svg+xml');
    if(doc.documentElement.localName!=='svg'){node.textContent='无法读取字形预览';return;}
    node.append(document.importNode(doc.documentElement,true));
    if(!node.classList.contains('font-thumbnail'))node.append(speedNode(value.font_id));
    if(value.missing?.length){const hint=document.createElement('span');hint.className='font-missing';hint.textContent='缺字：'+value.missing.join('')+'（方框占位）';node.append(hint);}
  }
  function applyCached(){
    for(const node of document.querySelectorAll('[data-font-preview]')){
      const text=node.dataset.previewText??sampleText();
      const value=cache.get(text+'\n'+node.dataset.fontPreview);
      if(value)renderSample(node,value);
    }
  }
  async function paint(){
    const revision=sampleRevision,groups=new Map();
    const valid=sampleText()&&Array.from(sampleText()).length<=24;
    byId('fontSampleHint').textContent=valid?'直接显示真实笔画；缺字用方框标记。':'请输入 1–24 个预览字。';
    applyCached();
    for(const node of document.querySelectorAll('[data-font-preview]')){
      const text=node.dataset.previewText??sampleText(),id=node.dataset.fontPreview,key=text+'\n'+id;
      if(!text||Array.from(text).length>24||cache.has(key)||pending.has(key))continue;
      if(!groups.has(text))groups.set(text,new Set());groups.get(text).add(id);
    }
    for(const [text,unique] of groups){
     const ids=[...unique];
     for(let start=0;start<ids.length;start+=12){
      const batch=ids.slice(start,start+12);
      batch.forEach(id=>pending.add(text+'\n'+id));
      try{
        const result=await api('/api/font-previews',{font_ids:batch,text});
        if(revision===sampleRevision){
          for(const value of result.previews){cache.set(text+'\n'+value.font_id,value);while(cache.size>384)cache.delete(cache.keys().next().value);}
          applyCached();
        }
      }catch(error){if(text===sampleText())byId('fontSampleHint').textContent='字形预览加载失败：'+error.message+'；可点击“刷新预览”重试。';}
      finally{batch.forEach(id=>pending.delete(text+'\n'+id));if(revision!==sampleRevision)schedule();}
     }
    }
  }
  function schedule(){clearTimeout(paintTimer);paintTimer=setTimeout(paint,25);}
  function choose(id){
    byId('font').value=id;updateProbabilities();focus();
  }
  function glyphCount(id){return catalog?.fonts?.[id]?.glyph_count;}
  function nearest(){
    const current=byId('font').value;
    return (catalog?.fonts?.[current]?.nearest||[]).filter(item=>item.font_id!==current&&fonts.some(font=>font.id===item.font_id)&&Number.isFinite(item.score))
      .sort((a,b)=>b.score-a.score).slice(0,6);
  }
  function renderSimilar(){
    const host=byId('fontSimilarList');host.replaceChildren();
    const matches=nearest();
    byId('fontSimilarHint').textContent=matches.length?'以当前预览字体为参照 · 相似度前六名':'当前字体暂无足够的共同字样本，暂不推荐。';
    for(const match of matches){
      const font=fonts.find(item=>item.id===match.font_id);
      const button=document.createElement('button');button.type='button';button.className='font-similar-choice';button.dataset.fontId=font.id;
      const name=document.createElement('span');name.textContent=font.name;
      const score=document.createElement('strong');score.textContent=match.score.toFixed(1)+' 分';
      button.append(name,score);button.title='预览 '+font.name;button.onclick=()=>choose(font.id);host.append(button);
    }
  }
  function focus(){
    const font=fonts.find(f=>f.id===byId('font').value),host=byId('fontFocus');host.replaceChildren();
    renderSimilar();
    if(!font){host.textContent='请选择一个字体查看预览。';return;}
    const name=document.createElement('p');name.className='font-card-name';name.textContent='当前候选 · '+font.name;
    const count=document.createElement('p');count.className='font-glyph-count';count.textContent=glyphCount(font.id)==null?'字数待分析':`包含 ${glyphCount(font.id).toLocaleString()} 个字`;
    host.append(name,count,sampleNode(font.id));
    const info=catalog?.fonts?.[font.id];
    if(info?.duplicate_of){const note=document.createElement('p');note.className='hint';note.textContent='此文件与字库中另一项高度重复，不作为推荐组合的变化来源。';host.append(note);}
    for(const card of byId('fontGallery').children)card.classList.toggle('is-current',card.dataset.fontId===font.id);
    if(sort.value==='similarity')gallery(true);
    schedule();
  }
  function gallery(reset=false){
    if(reset)page=0;
    const query=byId('fontSearch').value.trim().toLowerCase();
    let filtered=fonts.filter(f=>f.name.toLowerCase().includes(query));
    const matches=nearest(),scores=new Map(matches.map(item=>[item.font_id,item.score]));
    if(sort.value==='glyphs')filtered.sort((a,b)=>(glyphCount(b.id)??-1)-(glyphCount(a.id)??-1)||a.name.localeCompare(b.name));
    if(sort.value==='speed')filtered.sort((a,b)=>(catalog?.fonts?.[b.id]?.speed?.score??-1)-(catalog?.fonts?.[a.id]?.speed?.score??-1)||a.name.localeCompare(b.name));
    if(sort.value==='similarity')filtered=filtered.filter(font=>scores.has(font.id)).sort((a,b)=>scores.get(b.id)-scores.get(a.id));
    const current=fonts.find(font=>font.id===byId('font').value);
    byId('fontSortHint').textContent=sort.value==='similarity'?`参照：${current?.name||'未选择'} · 仅显示前六名，排除当前字体。`:'字数和速度排序显示全部字体，可翻页浏览。';
    page=Math.min(page,Math.max(0,Math.ceil(filtered.length/pageSize)-1));
    const host=byId('fontGallery');host.replaceChildren();
    for(const font of filtered.slice(page*pageSize,(page+1)*pageSize)){
      const card=document.createElement('article');card.className='font-card';card.dataset.fontId=font.id;
      card.classList.toggle('is-current',font.id===byId('font').value);
      const name=document.createElement('button');name.type='button';name.className='font-card-name text-button';name.textContent=font.name;name.onclick=()=>choose(font.id);
      const add=document.createElement('button');add.type='button';add.className='font-card-add secondary';
      const selected=selectedFonts.some(f=>f.font_id===font.id);add.textContent=selected?'已添加':'添加';add.disabled=selected||selectedFonts.length>=16;
      add.onclick=()=>{choose(font.id);byId('addFont').click();};
      const heading=document.createElement('div');heading.className='font-card-heading';heading.append(name,add);
      card.dataset.glyphCount=glyphCount(font.id)??'';
      const metrics=document.createElement('div');metrics.className='font-card-metrics';
      const count=document.createElement('span');count.className='font-glyph-count';count.textContent=glyphCount(font.id)==null?'字数待分析':`包含 ${glyphCount(font.id).toLocaleString()} 字`;
      metrics.append(count,speedNode(font.id));
      if(sort.value==='similarity'){const similarity=document.createElement('span');similarity.className='font-similarity-score';similarity.dataset.similarityScore=scores.get(font.id);similarity.textContent=`相似 ${scores.get(font.id).toFixed(1)} 分`;metrics.append(similarity);}
      const content=document.createElement('div');content.className='font-card-content';content.append(heading,metrics);
      const thumbnail=sampleNode(font.id,singleChar());thumbnail.tabIndex=0;thumbnail.setAttribute('role','button');thumbnail.setAttribute('aria-label','预览 '+font.name);thumbnail.onclick=()=>choose(font.id);thumbnail.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(font.id);}};
      card.append(thumbnail,content);host.append(card);
    }
    if(!filtered.length)host.textContent=sort.value==='similarity'?(matches.length?'前六名中没有匹配的字体，请更换关键词。':'当前字体暂无可用相似度，试试按字数或速度排序。'):'没有匹配的字体，请换一个关键词。';
    byId('fontPageInfo').textContent=filtered.length?`${page*pageSize+1}–${Math.min((page+1)*pageSize,filtered.length)} / ${filtered.length} 种字体`:'0 种字体';
    byId('fontPrev').disabled=page===0;byId('fontNext').disabled=(page+1)*pageSize>=filtered.length;schedule();
  }
  function renderRecommendation(){
    const group=catalog?.recommendations?.find(g=>g.id===byId('fontRecommendation').value);
    const host=byId('fontRecommendationPreview');host.replaceChildren();byId('applyFontRecommendation').disabled=!group;
    if(!group)return;
    const summary=document.createElement('p');summary.className='font-recommendation-score';
    summary.textContent=`最低相似评分 ${group.score}/100 · ${group.sample_characters} 个共同样本字 · ${group.shared_glyphs} 个共同字形`;
    host.append(summary);
    for(const member of group.mix){
      const row=document.createElement('div');row.className='font-recommendation-row';
      const label=document.createElement('p');label.className='font-card-name';label.textContent=member.name+' · '+member.weight+'%';
      row.append(label,sampleNode(member.font_id));host.append(row);
    }
    schedule();
  }
  async function loadRecommendations(){
    if(loaded)return;loaded=true;
    const revision=++catalogRevision;
    try{
      const result=await api('/api/font-recommendations');
      if(revision!==catalogRevision)return;
      catalog=result;
      byId('fontRecommendation').replaceChildren();
      for(const group of catalog.recommendations){const option=document.createElement('option');option.value=group.id;option.textContent=group.name+' · '+group.score+' 分';byId('fontRecommendation').append(option);}
      const s=catalog.summary;
      speedHint.textContent=catalog.speed_benchmark?`${s.speed_fonts} 个字体已评分 · 100 = 字库中位水平，越高越快。统一 5 mm 字高、绘图/移动 3000 mm/min，计入空移和抬落笔；模型估计，实际任务以 ETA 为准。`:'当前字库尚无速度评分。';
      byId('fontAnalysisHint').textContent=catalog.recommendations.length?`已核对 ${s.analyzed_fonts} 个字体文件，抽样 ${s.sample_characters} 个常用汉字；以 blood8 真迹系列为参照，排除高度重复项。评分不是同作者概率。`:(catalog.note||'当前字库没有可用的分析组合。');
      byId('fontRecommendation').disabled=!catalog.recommendations.length;renderRecommendation();gallery();focus();applyCached(sampleText());
    }catch(error){if(revision===catalogRevision){loaded=false;byId('fontAnalysisHint').textContent='相似组合暂不可用：'+error.message;}}
  }
  byId('fontPrev').onclick=()=>{page--;gallery();};byId('fontNext').onclick=()=>{page++;gallery();};
  function updateSingleChar(event){if(event.isComposing)return;byId('fontSingleChar').value=Array.from(byId('fontSingleChar').value).slice(0,1).join('');gallery();}
  byId('fontSingleChar').addEventListener('input',updateSingleChar);
  byId('fontSingleChar').addEventListener('compositionend',updateSingleChar);
  byId('fontSampleText').addEventListener('input',()=>{clearTimeout(inputTimer);inputTimer=setTimeout(()=>{cache.clear();schedule();},250);});
  byId('refreshFontSamples').onclick=()=>{cache.clear();schedule();if(!loaded)loadRecommendations();};
  byId('fontRecommendation').onchange=renderRecommendation;
  byId('applyFontRecommendation').onclick=()=>{
    const group=catalog?.recommendations?.find(g=>g.id===byId('fontRecommendation').value);if(!group)return;
    if(group.mix.some(m=>!fonts.some(f=>f.id===m.font_id))){notice('组合字体在当前字库不可用，请刷新后重试',true);return;}
    selectedFonts=group.mix.map(m=>({font_id:m.font_id,weight:m.weight}));selectedFontPreset='';byId('fontPresetName').value=group.name;
    invalidate();renderFontMix();renderFontPresets();
    notice('组合已应用；重新生成预览后用于下一任务。可点击“另存字体预设”保存。');
  };
  return {start(){gallery();focus();loadRecommendations();checkStatus();},refresh(){gallery(true);focus();},focus,
    refreshSelected(){gallery();focus();schedule();}};
})();
