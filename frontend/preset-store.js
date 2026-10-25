'use strict';
// Server is authoritative. Legacy localStorage entries remain as migration backups.
window.PresetStore=class PresetStore {
  constructor(api,storage){this.api=api;this.storage=storage;this.groups=null;this.pending=false;}
  kind(key){return key.includes('font-presets')?'font':key.includes('grid-presets')?'grid':'layout';}
  key(kind){return `plotter.${kind==='font'?'font':kind==='grid'?'grid':'layout'}-presets.v1`;}
  local(key){try{return JSON.parse(this.storage.getItem(key));}catch{return null;}}
  selection(key,id){try{this.storage.setItem(key+'.selected-server',JSON.stringify(id));}catch{/* Selection is optional. */}}
  read(key){
    const kind=this.kind(key),group=this.groups?.[kind];
    return {presets:group?.presets||[],selected:this.local(key+'.selected-server')??this.local(key)?.selected};
  }
  async init(){
    if(this.groups)return;
    const groups={};
    for(const kind of ['layout','grid','font']){
      const legacy=this.local(this.key(kind));
      if(legacy&&Array.isArray(legacy.presets)&&legacy.presets.length)groups[kind]=legacy;
    }
    if(Object.keys(groups).length){
      const result=await this.api('/api/presets/migrate',{groups});
      this.groups=result.groups;this.imported=result.imported;
      for(const [kind,id] of Object.entries(result.selected)){
        const key=this.key(kind);
        if(this.local(key+'.selected-server')===null)this.selection(key,id);
      }
    }else this.groups=(await this.api('/api/presets')).groups;
  }
  async save(key,presets,selected){
    if(!this.groups)throw new Error('服务器预设尚未载入，请刷新页面或重新连接服务');
    if(this.pending)throw new Error('预设正在保存，请稍后重试');
    this.pending=true;
    try{
      const kind=this.kind(key);
      const result=await this.api('/api/presets',{kind,revision:this.groups[kind].revision,presets});
      // Keep other categories' revisions as loaded: stale pages must fail rather than overwrite.
      this.groups[kind]=result.groups[kind];this.selection(key,selected);
    }finally{this.pending=false;}
  }
};
