'use strict';
// Browsing fonts only requests vector samples; it never submits a writing job.
window.FontBrowser=(()=>{
  const byId=id=>document.getElementById(id);
  const cache=new Map(),pending=new Set();
  let page=0,paintTimer,inputTimer,catalog=null,loaded=false;
  const pageSize=6;
  function sampleText(){return byId('fontSampleText').value.trim();}
  function sampleNode(id){const node=document.createElement('div');node.className='font-sample';node.dataset.fontPreview=id;node.textContent='加载字形…';return node;}
  function renderSample(node,value){
    node.replaceChildren();
    if(value.error){node.textContent='预览暂不可用：'+value.error;return;}
    const doc=new DOMParser().parseFromString(value.svg,'image/svg+xml');
    if(doc.documentElement.localName!=='svg'){node.textContent='无法读取字形预览';return;}
    node.append(document.importNode(doc.documentElement,true));
    if(value.missing?.length){const hint=document.createElement('span');hint.className='font-missing';hint.textContent='缺字：'+value.missing.join('')+'（方框占位）';node.append(hint);}
  }
  function applyCached(text){
    for(const node of document.querySelectorAll('[data-font-preview]')){
      const value=cache.get(text+'\n'+node.dataset.fontPreview);
      if(value)renderSample(node,value);
    }
  }
  async function paint(){
    const text=sampleText();
    if(!text||text.length>24){byId('fontSampleHint').textContent='请输入 1–24 个预览字。';return;}
    byId('fontSampleHint').textContent='直接显示当前字体的真实笔画；缺字用方框标记，不替换成其他字体。';
    applyCached(text);
    const ids=[...new Set([...document.querySelectorAll('[data-font-preview]')].map(n=>n.dataset.fontPreview))]
      .filter(id=>!cache.has(text+'\n'+id)&&!pending.has(text+'\n'+id));
    for(let start=0;start<ids.length;start+=12){
      const batch=ids.slice(start,start+12);
      batch.forEach(id=>pending.add(text+'\n'+id));
      try{
        const result=await api('/api/font-previews',{font_ids:batch,text});
        for(const value of result.previews){cache.set(text+'\n'+value.font_id,value);while(cache.size>384)cache.delete(cache.keys().next().value);}
        if(text===sampleText())applyCached(text);
      }catch(error){if(text===sampleText())byId('fontSampleHint').textContent='字形预览加载失败：'+error.message+'；可点击“刷新预览”重试。';}
      finally{batch.forEach(id=>pending.delete(text+'\n'+id));}
    }
  }
  function schedule(){clearTimeout(paintTimer);paintTimer=setTimeout(paint,25);}
  function choose(id){
    if(![...byId('font').options].some(o=>o.value===id)){byId('fontSearch').value='';filterFonts();}
    byId('font').value=id;updateProbabilities();focus();
  }
  function focus(){
    const font=fonts.find(f=>f.id===byId('font').value),host=byId('fontFocus');host.replaceChildren();
    if(!font)return;
    const name=document.createElement('p');name.className='font-card-name';name.textContent='当前候选 · '+font.name;
    host.append(name,sampleNode(font.id));
    const info=catalog?.fonts?.[font.id];
    if(info?.duplicate_of){const note=document.createElement('p');note.className='hint';note.textContent='此文件与字库中另一项高度重复，不作为推荐组合的变化来源。';host.append(note);}
    for(const card of byId('fontGallery').children)card.classList.toggle('is-current',card.dataset.fontId===font.id);
    schedule();
  }
  function gallery(reset=false){
    if(reset)page=0;
    const query=byId('fontSearch').value.trim().toLowerCase();
    const filtered=fonts.filter(f=>f.name.toLowerCase().includes(query));
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
      card.append(heading,sampleNode(font.id));host.append(card);
    }
    if(!filtered.length)host.textContent='没有匹配的字体，请换一个关键词。';
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
    try{
      catalog=await api('/api/font-recommendations');
      byId('fontRecommendation').replaceChildren();
      for(const group of catalog.recommendations){const option=document.createElement('option');option.value=group.id;option.textContent=group.name+' · '+group.score+' 分';byId('fontRecommendation').append(option);}
      const s=catalog.summary;
      byId('fontAnalysisHint').textContent=catalog.recommendations.length?`已核对 ${s.analyzed_fonts} 个字体文件，抽样 ${s.sample_characters} 个常用汉字；以 blood8 真迹系列为参照，排除高度重复项。评分不是同作者概率。`:(catalog.note||'当前字库没有可用的分析组合。');
      byId('fontRecommendation').disabled=!catalog.recommendations.length;renderRecommendation();focus();
    }catch(error){loaded=false;byId('fontAnalysisHint').textContent='相似组合暂不可用：'+error.message;}
  }
  byId('fontPrev').onclick=()=>{page--;gallery();};byId('fontNext').onclick=()=>{page++;gallery();};
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
  return {start(){gallery();focus();loadRecommendations();},refresh(){gallery(true);focus();},focus,
    refreshSelected(){gallery();focus();schedule();}};
})();
