'use strict';
window.NotebookSizing={
  compute({height,marginTop,marginBottom,rows,size},mode,ratio){
    const available=height-marginTop-marginBottom;
    if(!Number.isFinite(available)||available<=0)throw new Error('请为上下边距之间留出可写高度');
    if(!['rows','size'].includes(mode)||!Number.isFinite(ratio)||ratio<1||ratio>4)throw new Error('请选择固定行数或字高，并设置 1–4 倍行距');
    let pitch;
    if(mode==='rows'){
      if(!Number.isInteger(rows)||rows<1||rows>200)throw new Error('固定行数须为 1–200 的整数');
      pitch=available/rows;size=pitch/ratio;
      if(size<2||size>20)throw new Error('自动字高超出 2–20 mm，请调整行数、松紧或上下边距');
    }else{
      if(!Number.isFinite(size)||size<2||size>20)throw new Error('固定字高须为 2–20 mm');
      pitch=size*ratio;rows=Math.floor((available+.000001)/pitch);
      if(rows<1||rows>200)throw new Error('自动行数超出 1–200 行，请调整字高、松紧或上下边距');
    }
    return {rows,size,pitch,gap:pitch-size};
  }
};
