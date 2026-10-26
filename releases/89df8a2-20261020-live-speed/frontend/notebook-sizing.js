'use strict';
window.NotebookSizing={
  compute({height,marginTop,marginBottom,rows,size,latinSize},mode,ratio,overlap=false){
    const available=height-marginTop-marginBottom;
    if(!Number.isFinite(available)||available<=0)throw new Error('请为上下边距之间留出可写高度');
    if(!['rows','size'].includes(mode)||!Number.isFinite(ratio)||ratio<1||ratio>4)throw new Error('请选择固定行数或字高，并设置 1–4 倍行距');
    // The English height keeps its ratio to the Chinese height when either one is derived.
    const latinRatio=Number.isFinite(latinSize)&&size>0&&latinSize>0?latinSize/size:1;
    let pitch;
    if(mode==='rows'){
      if(!Number.isInteger(rows)||rows<1||rows>200)throw new Error('固定行数须为 1–200 的整数');
      pitch=available/rows;size=pitch/ratio;latinSize=size*latinRatio;
      // Overlap mode is the explicit opt-out from the height limits.
      if(!overlap&&(Math.min(size,latinSize)<2||Math.max(size,latinSize)>20))throw new Error('自动字高超出 2–20 mm，请调整行数、松紧或上下边距');
      if(!overlap&&latinSize>pitch+.001)throw new Error('英文字高超过行距，请减小英文字高或放宽行间松紧');
    }else{
      if(!Number.isFinite(size)||size<2||size>20)throw new Error('固定字高须为 2–20 mm');
      if(!Number.isFinite(latinSize)||latinSize<2||latinSize>20)throw new Error('英文固定字高须为 2–20 mm');
      pitch=Math.max(size,latinSize)*ratio;rows=Math.floor((available+.000001)/pitch);
      if(!overlap&&(rows<1||rows>200))throw new Error('自动行数超出 1–200 行，请调整字高、松紧或上下边距');
    }
    // Overlap mode always reports at least one row, because the row count is only a page estimate.
    return {rows:Math.max(1,rows),size,latinSize,pitch,gap:pitch-Math.max(size,latinSize)};
  }
};
