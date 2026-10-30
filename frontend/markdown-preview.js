'use strict';
// Live markdown preview for the enlarged editor. Renders the constructs the
// writing backend accepts (see backend.document.markdown_text and mathtext.items):
// headings, emphasis, links, lists, quotes, tables, fenced code, $…$ / $$…$$ and
// \(…\) / \[…\] formulas, plus the explicit page breaks (\f, \newpage, \pagebreak).
// Formulas stay visible as styled chips instead of being flattened into plain text.
window.renderMarkdown=(()=>{
  const ESC={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const esc=s=>s.replace(/[&<>"']/g,c=>ESC[c]);
  // Same entity set backend.document.markdown_text decodes before writing.
  const ENTITIES={'&lt;':'<','&gt;':'>','&amp;':'&','&quot;':'"','&#39;':"'",'&nbsp;':' '};
  const decode=s=>s.replace(/&(?:lt|gt|amp|quot|#39|nbsp);/g,e=>ENTITIES[e]);
  const FRONT_MATTER=/^\ufeff?---[ \t]*\n[A-Za-z_][^\n:]*:[^\n]*\n(?:[^\n]*\n)*?---[ \t]*\n/;
  const HR=/^ {0,3}((-\s*){3,}|(\*\s*){3,}|(_\s*){3,})$/;
  const FENCE=/^ {0,3}(`{3,}|~{3,})\s*([^`]*)$/;
  const ITEM=/^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const SEPARATOR=/^\s*\|?[\s:|-]*-[\s:|-]*$/;
  const itemIndent=line=>line.replace(/\t/g,'    ').match(/^\s*/)[0].length;

  function inline(text){
    const stash=[];
    const keep=html=>{stash.push(html);return `\u0000${stash.length-1}\u0000`;};
    // \$ mirrors the backend's literal dollar; park it so formula pairs never see it.
    let out=esc(text).replace(/\\\$/g,'\u0002');
    out=out.replace(/(`+)([\s\S]*?)\1/g,(m,fence,code)=>keep(`<code>${code.trim()}</code>`));
    out=out.replace(/\$\$([\s\S]+?)\$\$/g,(m,f)=>keep(`<code class="md-formula md-display">${f.trim()}</code>`));
    out=out.replace(/\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g,(m,a,b)=>keep(`<code class="md-formula">${(a??b).trim()}</code>`));
    out=out.replace(/\$([^$\n]+?)\$/g,(m,f)=>keep(`<code class="md-formula">${f.trim()}</code>`));
    out=out.replace(/!\[([^\]]*)\]\([^)\n]*\)/g,(m,alt)=>alt||'[图片]');
    out=out.replace(/\[([^\]]*)\]\(([^)\s]*)[^)\n]*\)/g,(m,label,href)=>href?`<a href="${href}" target="_blank" rel="noopener">${label||href}</a>`:label);
    out=out.replace(/\*\*([\s\S]+?)\*\*|__([\s\S]+?)__/g,(m,a,b)=>`<strong>${a??b}</strong>`);
    out=out.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g,(m,pre,inner)=>`${pre}<em>${inner}</em>`);
    out=out.replace(/(^|[^_\w])_([^_\n]+?)_(?!\w)/g,(m,pre,inner)=>`${pre}<em>${inner}</em>`);
    out=out.replace(/~~([^~\n]+?)~~/g,(m,inner)=>`<del>${inner}</del>`);
    return out.replace(/\u0000(\d+)\u0000/g,(m,i)=>stash[+i]).replace(/\u0002/g,'$');
  }

  function renderList(lines,i){
    const first=lines[i].match(ITEM),ordered=/\d/.test(first[2]),base=itemIndent(lines[i]);
    let html=ordered?'<ol>':'<ul>';
    while(i<lines.length){
      const m=lines[i].match(ITEM);
      if(!m||itemIndent(lines[i])<base||/\d/.test(m[2])!==ordered)break;
      if(itemIndent(lines[i])>base){
        const [sub,next]=renderList(lines,i);
        html=html.replace(/<\/li>$/,`${sub}</li>`);i=next;continue;
      }
      html+=`<li>${inline(m[3])}</li>`;i++;
    }
    return [html+(ordered?'</ol>':'</ul>'),i];
  }

  function block(lines){
    const out=[];
    let i=0;
    const tableNext=()=>i+1<lines.length&&lines[i+1].includes('|')&&SEPARATOR.test(lines[i+1]);
    while(i<lines.length){
      const line=lines[i],trimmed=line.trim();
      const fence=line.match(FENCE);
      if(fence){
        const close=new RegExp(`^ {0,3}\\${fence[1][0]}{${fence[1].length},}\\s*$`);
        const body=[];
        i++;
        while(i<lines.length&&!close.test(lines[i]))body.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(body.join('\n'))}</code></pre>`);continue;
      }
      if(trimmed.startsWith('$$')){
        const single=trimmed.match(/^\$\$(.+)\$\$$/);
        const body=[];
        if(single){body.push(single[1]);i++;}
        else{for(i++;i<lines.length&&!lines[i].trim().endsWith('$$');i++)body.push(lines[i]);i++;}
        out.push(`<div class="md-formula-block"><code>${esc(body.join('\n').trim())}</code></div>`);continue;
      }
      const heading=line.match(/^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if(heading){out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);i++;continue;}
      if(HR.test(line)){out.push('<hr>');i++;continue;}
      if(/^ {0,3}>/.test(line)){
        const quoted=[];
        while(i<lines.length&&/^ {0,3}>/.test(lines[i]))quoted.push(lines[i++].replace(/^ {0,3}> ?/,''));
        out.push(`<blockquote>${block(quoted)}</blockquote>`);continue;
      }
      if(line.includes('|')&&tableNext()){
        const cells=row=>row.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>c.trim());
        const align=cells(lines[i+1]).map(c=>c.startsWith(':')&&c.endsWith(':')?'center':c.endsWith(':')?'right':c.startsWith(':')?'left':'');
        const head=cells(lines[i]);i+=2;
        const rows=[];
        while(i<lines.length&&lines[i].includes('|')&&lines[i].trim())rows.push(cells(lines[i++]));
        const cell=(content,n,tag)=>`<${tag}${align[n]?` style="text-align:${align[n]}"`:''}>${inline(content)}</${tag}>`;
        out.push(`<div class="md-table"><table><thead><tr>${head.map((c,n)=>cell(c,n,'th')).join('')}</tr></thead><tbody>${
          rows.map(row=>`<tr>${head.map((_,n)=>cell(row[n]||'',n,'td')).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      const item=line.match(ITEM);
      if(item){const [html,next]=renderList(lines,i);out.push(html);i=next;continue;}
      if(!trimmed){i++;continue;}
      const para=[];
      while(i<lines.length&&lines[i].trim()&&!FENCE.test(lines[i])&&!HR.test(lines[i])
        &&!/^ {0,3}(#{1,6}\s|>)/.test(lines[i])&&!ITEM.test(lines[i])
        &&!(lines[i].trim().startsWith('$$'))&&!(lines[i].includes('|')&&tableNext()))para.push(lines[i++]);
      if(!para.length)i++;
      out.push(`<p>${para.map(inline).join('<br>')}</p>`);
    }
    return out.join('');
  }

  return text=>{
    let source=decode(String(text??'')).replace(/\r\n?/g,'\n').replace(FRONT_MATTER,'');
    const sections=source.split(/\f|\\(?:newpage|pagebreak)\b/);
    if(!sections.some(section=>section.trim()))return '<p class="md-empty">暂无内容</p>';
    return sections.map(section=>section.trim()?block(section.split('\n')):'').join('<hr class="md-pagebreak">');
  };
})();
