"""Bounded LaTeX math parser. Output is vector strokes, never executable TeX."""
import re

from backend.mathtext import Box, COMMANDS, SYMBOLS, join, symbol
from backend.math_symbols import DELIMITERS, fence


FUNCTIONS = set('sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth log ln lg exp lim limsup liminf max min sup inf det dim gcd hom ker arg deg Pr'.split())
LIMIT_OPERATORS = set('sum prod bigcup bigcap lim limsup liminf max min sup inf'.split())
ENVIRONMENTS = {'matrix':('', ''), 'pmatrix':('(', ')'), 'bmatrix':('[', ']'),
                'Bmatrix':('{', '}'), 'vmatrix':('|', '|'), 'Vmatrix':('‖', '‖'),
                'smallmatrix':('', ''), 'cases':('{', '.'), 'aligned':('', ''),
                'gathered':('', ''), 'split':('', ''), 'array':('', ''),
                'equation':('', ''), 'equation*':('', '')}


def bounds_box(width, height, baseline, paths):
    """Include protruding accents, scripts and negative glue in the box bounds."""
    points = [p for stroke in paths for p in stroke]
    left = min([0.] + [x for x,y in points])
    top = min([0.] + [y for x,y in points])
    right = max([width] + [x for x,y in points])
    bottom = max([height] + [y for x,y in points])
    box = Box(right-left, bottom-top, baseline-top, paths)
    box.paths = box.placed(-left, -top)
    return box


def fenced(body, left, right, size):
    height = max(size, body.height + size*.16)
    y = (height-body.height)/2
    lw, lp = fence(left, size, height) if left else (0., [])
    rw, rp = fence(right, size, height) if right else (0., [])
    gap = size*.08
    dx = lw + (gap if lw else 0.)
    paths = lp + body.placed(dx,y)
    rx = dx + body.width + (gap if rw else 0.)
    paths.extend(Box(rw,height,0,rp).placed(rx))
    return Box(rx+rw,height,body.baseline+y,paths)


class Parser:
    def __init__(self, source, glyph, display=False):
        source = re.sub(r'<sub>(.*?)</sub>', r'_{\1}', source)
        source = re.sub(r'<sup>(.*?)</sup>', r'^{\1}', source)
        if not source.strip() or len(source)>1000:
            raise ValueError('公式需为 1–1000 个字符')
        self.source, self.glyph, self.pos, self.display = source, glyph, 0, display
        self.cells = 0

    def spaces(self):
        while self.pos<len(self.source):
            if self.source[self.pos].isspace(): self.pos+=1
            elif self.source[self.pos]=='%':
                end=self.source.find('\n',self.pos)
                self.pos=len(self.source) if end<0 else end+1
            else: break

    def at(self, value):
        if not self.source.startswith(value,self.pos): return False
        end=self.pos+len(value)
        return not (value.startswith('\\') and value[-1].isalpha() and
                    end<len(self.source) and self.source[end].isalpha())

    def command(self):
        match=re.match(r'\\([A-Za-z]+|[^\n])',self.source[self.pos:])
        if not match: raise ValueError('公式末尾的反斜杠无效')
        self.pos+=len(match[0])
        return match[1]

    def raw_group(self):
        self.spaces()
        match=re.match(r'\{([^{}]*)\}',self.source[self.pos:])
        if not match: raise ValueError('公式命令缺少花括号参数')
        self.pos+=len(match[0])
        return match[1]

    def group(self,size,depth):
        self.spaces()
        if self.pos>=len(self.source): raise ValueError('公式缺少参数或上下标内容')
        if self.source[self.pos]=='{':
            self.pos+=1
            box=self.sequence(size,depth+1,stops=('}',))
            if self.pos>=len(self.source) or self.source[self.pos]!='}': raise ValueError('公式缺少右花括号 }')
            self.pos+=1
            return box
        return self.atom(size,depth+1)

    def sequence(self,size,depth=0,stops=()):
        if depth>12: raise ValueError('公式嵌套超过 12 层')
        initial_style=self.display
        boxes=[]
        while True:
            self.spaces()
            if self.pos>=len(self.source) or any(self.at(stop) for stop in stops): break
            base=Box(0,size,size,[]) if self.source[self.pos] in '^_' else self.group(size,depth)
            scripts={}
            limits=base.limits
            self.spaces()
            while self.pos<len(self.source):
                if self.at(r'\limits') or self.at(r'\nolimits'):
                    limits=self.command()=='limits'
                    self.spaces(); continue
                if self.source[self.pos] not in '^_': break
                kind=self.source[self.pos]; self.pos+=1
                if kind in scripts: raise ValueError('公式的上下标重复，请用花括号分组')
                scripts[kind]=self.group(size*.62,depth+1)
                self.spaces()
            if scripts: base=self.script(base,scripts,size,limits)
            boxes.append(base)
        self.display=initial_style
        return self.math_join(boxes,size)

    def math_join(self,boxes,size):
        if not boxes: return Box(0,0,0,[])
        baseline=max(b.baseline for b in boxes)
        height=baseline+max(b.height-b.baseline for b in boxes)
        paths=[]; x=0.; previous=None
        for box in boxes:
            if previous is not None and not (box.glue or previous.glue): x+=size*.1
            paths.extend(box.placed(x,baseline-box.baseline))
            x+=box.width
            previous=box
        return bounds_box(max(0.,x),height,baseline,paths)

    def script(self,base,scripts,size,limits):
        sup,sub=scripts.get('^'),scripts.get('_')
        if limits:
            width=max([base.width]+[s.width for s in scripts.values()])
            top=sup.height+size*.15 if sup else 0.
            paths=base.placed((width-base.width)/2,top)
            if sup: paths+=sup.placed((width-sup.width)/2)
            height=top+base.height
            if sub:
                paths+=sub.placed((width-sub.width)/2,height+size*.15)
                height+=size*.15+sub.height
            return bounds_box(width,height,base.baseline+top,paths)
        sy=min(-size*.12,base.baseline-size*.65-(sup.height if sup else 0.))
        by=max(base.baseline-size*.1,(sy+sup.height+size*.18) if sup else 0.)
        top=min(0.,sy) if sup else 0.
        paths=base.placed(0,-top)
        height=base.height
        for box,y in ((sup,sy),(sub,by)):
            if box:
                paths+=box.placed(base.width+size*.06,y-top)
                height=max(height,y+box.height)
        return bounds_box(base.width+size*.06+max(s.width for s in scripts.values()),height-top,base.baseline-top,paths)

    def delimiter(self):
        self.spaces()
        if self.pos>=len(self.source): raise ValueError('公式缺少界定符')
        char=self.source[self.pos]
        if char=='\\':
            command=self.command()
            char=DELIMITERS.get(command,{'{':'{','}':'}','|':'‖','backslash':'\\'}.get(command))
        else: self.pos+=1
        if char not in ('.','(',')','[',']','{','}','|','‖','<','>','⟨','⟩','⌊','⌋','⌈','⌉','/','\\'):
            raise ValueError('不支持的公式界定符')
        return char

    def environment(self,size,depth):
        name=self.raw_group()
        if name not in ENVIRONMENTS: raise ValueError('暂不支持 LaTeX 环境：'+name)
        alignment=self.raw_group().replace(' ','') if name=='array' else ''
        if name=='array' and (not alignment or len(alignment)>12 or any(c not in 'lcr' for c in alignment)):
            raise ValueError('array 列格式需为 1–12 个 l/c/r')
        cell_size=size*.75 if name=='smallmatrix' else size
        rows=[]; gaps=[]
        while True:
            row=[]
            while True:
                self.cells+=1
                if self.cells>144: raise ValueError('公式表格最多 144 个单元格')
                row.append(self.sequence(cell_size,depth+1,stops=('&',r'\\',r'\cr',r'\end')))
                self.spaces()
                if self.pos>=len(self.source): raise ValueError('公式环境未闭合：'+name)
                if self.source[self.pos]=='&':
                    self.pos+=1
                    if len(row)>=12: raise ValueError('公式表格最多 12 列')
                    continue
                break
            rows.append(row)
            if len(rows)>24: raise ValueError('公式表格最多 24 行')
            if self.at(r'\end'):
                self.command()
                if self.raw_group()!=name: raise ValueError('公式环境的 begin/end 不匹配：'+name)
                break
            if not (self.at(r'\\') or self.at(r'\cr')): raise ValueError('公式表格行分隔符无效')
            self.command(); self.spaces()
            extra=0.
            if self.pos<len(self.source) and self.source[self.pos]=='[':
                match=re.match(r'\[\s*(\d+(?:\.\d+)?)\s*(pt|em|ex|mm)\s*\]',self.source[self.pos:])
                if not match: raise ValueError('公式行距支持 [数字 pt/em/ex/mm]')
                extra=float(match[1])*{'pt':size/10,'em':size,'ex':size*.5,'mm':1.}[match[2]]
                if extra>size*3: raise ValueError('公式额外行距不能超过 3 em')
                self.pos+=len(match[0])
            gaps.append(extra)
            self.spaces()
            # TeX permits a final row terminator, but it must not add a blank row.
            if self.at(r'\end'):
                self.command()
                if self.raw_group()!=name: raise ValueError('公式环境的 begin/end 不匹配：'+name)
                break
        columns=max(map(len,rows))
        if alignment and columns!=len(alignment): raise ValueError('array 列数与列格式不匹配')
        if name=='cases' and columns>2: raise ValueError('cases 最多两列')
        if name in ('gathered','equation','equation*') and columns>1: raise ValueError(name+' 不支持 & 分列')
        if name in ('equation','equation*') and len(rows)>1: raise ValueError(name+' 不支持多行，请使用 aligned')
        widths=[max((r[i].width for r in rows if i<len(r)),default=0.) for i in range(columns)]
        col_gaps=[cell_size*(.18 if name in ('aligned','split') and i%2==0 else .8 if name!='cases' else .6)
                  for i in range(columns-1)]
        paths=[]; y=0.
        for row_index,row in enumerate(rows):
            baseline=max(cell_size,max(b.baseline for b in row))
            height=max(cell_size,baseline+max(b.height-b.baseline for b in row))
            x=0.
            for i,box in enumerate(row):
                align=alignment[i] if alignment else ('l' if name=='cases' else ('r' if i%2==0 else 'l') if name in ('aligned','split') else 'c')
                offset=(widths[i]-box.width)*({'l':0.,'c':.5,'r':1.}[align])
                paths+=box.placed(x+offset,y+baseline-box.baseline)
                x+=widths[i]+(col_gaps[i] if i<columns-1 else 0.)
            y+=height
            if row_index<len(rows)-1: y+=cell_size*.35+gaps[row_index]
        body=Box(sum(widths)+sum(col_gaps),y,y/2+size*.25,paths)
        return fenced(body,*ENVIRONMENTS[name],size) if any(ENVIRONMENTS[name]) else body

    def text(self,size):
        self.spaces()
        if self.pos>=len(self.source) or self.source[self.pos]!='{': raise ValueError('\\text 的文字请放在花括号中')
        self.pos+=1
        letters=[];level=1
        while self.pos<len(self.source):
            letter=self.source[self.pos];self.pos+=1
            if letter=='\\':
                if self.pos>=len(self.source) or self.source[self.pos] not in '{}%$#_& ': raise ValueError('\\text 中暂只支持纯文本及转义标点')
                letter=self.source[self.pos];self.pos+=1
            elif letter=='{':
                level+=1
                if level>12: raise ValueError('公式嵌套超过 12 层')
                continue
            elif letter=='}':
                level-=1
                if not level: break
                continue
            letters.append(Box(size*.35,size,size,[]) if letter.isspace() else self.glyph(letter,size))
        if level: raise ValueError('\\text 缺少右花括号 }')
        return join(letters,size*.05)

    def atom(self,size,depth):
        if depth>12: raise ValueError('公式嵌套超过 12 层')
        if self.pos>=len(self.source): raise ValueError('公式缺少参数')
        char=self.source[self.pos]
        if char=='{': return self.group(size,depth)
        if char in '^_}&': raise ValueError('公式上下标、花括号或对齐符位置无效')
        if char!='\\':
            self.pos+=1
            return self.character(char,size)
        command=self.command()
        if command in ('frac','dfrac','tfrac','binom','dbinom','tbinom'):
            factor=1. if command.startswith('d') or self.display and not command.startswith('t') else .72
            # Fraction contents are text style, preventing nested display fractions growing exponentially.
            style=self.display;self.display=False
            try:
                numerator=self.group(size*factor,depth+1)
                denominator=self.group(size*factor,depth+1)
            finally: self.display=style
            width=max(numerator.width,denominator.width)+size*.3
            line_y=numerator.height+size*.15
            paths=numerator.placed((width-numerator.width)/2)
            if 'frac' in command: paths.append([(0.,line_y),(width,line_y)])
            paths+=denominator.placed((width-denominator.width)/2,line_y+size*.15)
            body=Box(width,line_y+size*.15+denominator.height,line_y+size*.25,paths)
            return fenced(body,'(',')',size) if 'binom' in command else body
        if command=='sqrt':
            self.spaces();index=None
            if self.pos<len(self.source) and self.source[self.pos]=='[':
                self.pos+=1
                index=self.sequence(size*.45,depth+1,stops=(']',))
                if self.pos>=len(self.source): raise ValueError('根号次数缺少右方括号 ]')
                self.pos+=1
                if not index.paths: raise ValueError('根号次数不能为空')
            body=self.group(size,depth+1)
            pad=size*.15
            offset=max(0.,index.width-size*.28) if index else 0.
            paths=body.placed(offset+size*.65,pad)
            paths.append([(offset,body.height*.55),(offset+size*.18,body.height*.45),
                          (offset+size*.35,body.height+pad),(offset+size*.6,0.),(offset+body.width+size*.7,0.)])
            if index: paths+=index.placed(0,-size*.12)
            return bounds_box(offset+body.width+size*.75,body.height+pad,body.baseline+pad,paths)
        if command=='begin': return self.environment(size,depth)
        if command=='left':
            left=self.delimiter()
            body=self.sequence(size,depth+1,stops=(r'\right',))
            if not self.at(r'\right'): raise ValueError('\\left 缺少配对的 \\right')
            self.command();right=self.delimiter()
            return fenced(body,left,right,size)
        if command in ('right','end','limits','nolimits','\\','cr'):
            raise ValueError('LaTeX 命令位置不正确：\\'+command)
        if command in ('big','Big','bigg','Bigg','bigl','bigr','Bigl','Bigr','biggl','biggr','Biggl','Biggr'):
            factor={'big':1.2,'Big':1.6,'bigg':2.,'Bigg':2.4}[command.rstrip('lr')]
            w,p=fence(self.delimiter(),size,size*factor)
            return Box(w,size*factor,size*(factor*.5+.5),p)
        if command in ('text','textrm','textnormal','mbox'): return self.text(size)
        if command in ('mathrm','mathbf','mathit','mathsf','mathtt','mathnormal','mathcal','mathbb','boldsymbol','bm','operatorname','mathop','mathrel','mathbin','mathord'):
            # Styles share the chosen single-line font; preserve the actual characters.
            starred=command=='operatorname' and self.pos<len(self.source) and self.source[self.pos]=='*'
            if starred: self.pos+=1
            box=self.group(size,depth+1)
            box.limits=self.display and (starred or command=='mathop')
            return box
        if command in ('displaystyle','textstyle','scriptstyle','scriptscriptstyle'):
            if command in ('scriptstyle','scriptscriptstyle'): raise ValueError('请使用上下标语法 ^{…} / _{…} 调整脚标')
            self.display=command=='displaystyle'
            return Box(0,0,0,[],glue=True)
        if command in FUNCTIONS:
            box=join([self.glyph(c,size) for c in command],size*.05)
            box.limits=self.display and command in LIMIT_OPERATORS
            return box
        if command in ('mod','bmod','pmod'):
            word=join([self.glyph(c,size) for c in 'mod'],size*.05)
            return fenced(join([word,self.group(size,depth+1)],size*.35),'(',')',size) if command=='pmod' else word
        if command in ('Re','Im'):
            return join([self.glyph(c,size) for c in command],size*.05)
        if command in ('overline','bar','underline','vec','overrightarrow','overleftarrow','hat','widehat','tilde','widetilde','dot','ddot','overbrace','underbrace'):
            body=self.group(size,depth+1)
            underneath=command in ('underline','underbrace')
            accent=size*(.3 if command in ('hat','widehat','vec','overrightarrow','overleftarrow','overbrace','underbrace') else .2)
            width=max(body.width,size*.3)
            paths=body.placed((width-body.width)/2,0 if underneath else accent)
            y=body.height+size*.08 if underneath else accent-size*.08
            if command in ('hat','widehat'): marks=[[(0,y),(width/2,0),(width,y)]]
            elif command in ('tilde','widetilde'): marks=[[(0,y*.7),(width*.25,0),(width*.75,y),(width,y*.3)]]
            elif command in ('dot','ddot'):
                centers=[width/2] if command=='dot' else [width*.32,width*.68]
                marks=[[(x-size*.015,0),(x+size*.015,0)] for x in centers]
            elif command in ('overbrace','underbrace'):
                cy=body.height+accent if underneath else 0.
                marks=[[(0,y),(0,(y+cy)/2),(width*.4,(y+cy)/2),(width/2,cy),
                        (width*.6,(y+cy)/2),(width,(y+cy)/2),(width,y)]]
            else:
                marks=[[(0,y),(width,y)]]
                if command in ('vec','overrightarrow'): marks+=[[(width-size*.22,y-size*.12),(width,y),(width-size*.22,y+size*.05)]]
                if command=='overleftarrow': marks+=[[(size*.22,y-size*.12),(0,y),(size*.22,y+size*.05)]]
            box=bounds_box(width,body.height+accent,body.baseline+(0 if underneath else accent),paths+marks)
            box.limits=command in ('overbrace','underbrace')
            return box
        if command in ('overset','underset','stackrel'):
            annotation=self.group(size*.62,depth+1)
            base=self.group(size,depth+1)
            return self.script(base,{'_' if command=='underset' else '^':annotation},size,True)
        if command=='substack':
            self.spaces()
            if self.pos>=len(self.source) or self.source[self.pos]!='{': raise ValueError('\\substack 缺少花括号参数')
            self.pos+=1;rows=[]
            while True:
                rows.append(self.sequence(size,depth+1,stops=('}',r'\\',r'\cr')))
                if len(rows)>24: raise ValueError('公式最多 24 行')
                if self.pos>=len(self.source): raise ValueError('\\substack 缺少右花括号 }')
                if self.source[self.pos]=='}':
                    self.pos+=1;break
                self.command()
            width=max(r.width for r in rows);y=0.;paths=[]
            for row in rows:
                paths+=row.placed((width-row.width)/2,y)
                y+=row.height+size*.2
            return Box(width,y-size*.2,(y-size*.2)/2+min(size*.25,(y-size*.2)/2),paths)
        if command=='boxed':
            body=self.group(size,depth+1);pad=size*.2
            width,height=body.width+2*pad,body.height+2*pad
            return Box(width,height,body.baseline+pad,body.placed(pad,pad)+[[(0,0),(width,0),(width,height),(0,height),(0,0)]])
        if command in ('phantom','hphantom','vphantom'):
            body=self.group(size,depth+1)
            return Box(0 if command=='vphantom' else body.width,
                       0 if command=='hphantom' else body.height,0 if command=='hphantom' else body.baseline,[])
        if command=='not':
            body=self.group(size,depth+1)
            return Box(body.width,body.height,body.baseline,body.paths+[
                [(body.width*.7,0),(body.width*.3,body.height)]])
        if command in (',',':',';',' ','quad','qquad','!','enspace','thinspace','medspace','thickspace'):
            width={',':1/6,':':2/9,';':5/18,' ':1/3,'quad':1.,'qquad':2.,'!':-1/6,'enspace':.5,
                   'thinspace':1/6,'medspace':2/9,'thickspace':5/18}[command]*size
            return Box(width,0,0,[],glue=True)
        if command in DELIMITERS or command in ('{','}','|'):
            char=DELIMITERS.get(command,'‖' if command=='|' else command)
            w,p=fence(char,size,size)
            return Box(w,size,size,p)
        if command in ('iint','iiint','oint','bigcup','bigcap'):
            char={'bigcup':'∪','bigcap':'∩'}.get(command,'∫')
            scale=1.35 if self.display else 1.
            count={'iint':2,'iiint':3}.get(command,1)
            box=join([symbol(char,size*scale) for _ in range(count)],-size*.35 if count>1 else 0.)
            if command=='oint':
                from backend.math_symbols import oval
                box.paths.append([(x*size*scale,y*size*scale) for x,y in oval(.48,.5,.23,.18)])
            box.limits=self.display and command in LIMIT_OPERATORS
            return box
        if command in COMMANDS:
            box=self.character(COMMANDS[command],size*(1.35 if self.display and command in ('sum','prod','int') else 1.))
            box.limits=self.display and command in LIMIT_OPERATORS
            return box
        if command in '{}%$#_&': return self.character(command,size)
        raise ValueError('暂不支持 LaTeX 命令：\\'+command)

    def character(self,char,size):
        if char in '()[]|{}\\':
            w,p=fence(char,size,size)
            return Box(w,size,size,p)
        return symbol(char,size) if char in SYMBOLS else self.glyph(char,size)
