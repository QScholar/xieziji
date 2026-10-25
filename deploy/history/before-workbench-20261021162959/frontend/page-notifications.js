'use strict';
// Completion is the controller's physical completion state, never an ACK count.
window.PageNotifications = class PageNotifications {
  constructor({hint, toggle, test, completion, storage, notify}={}) {
    Object.assign(this,{hint,toggle,test,completion,storage});
    try{this.storage=storage??window.localStorage;}catch(error){/* Storage can be blocked independently of notifications. */}
    this.key='plotter.page-notifications.v1';this.memory={enabled:false,jobs:{}};
    this.inflight=new Set();this.registration=null;
    this.notify=notify||((title,options)=>this.show(title,options));
    this.toggle.addEventListener('click',()=>this.toggleEnabled());
    this.test.addEventListener('click',()=>this.testNotification());
    window.addEventListener('storage',event=>{if(event.key===this.key)this.render();});
    window.addEventListener('focus',()=>this.render());
    this.render();
  }
  read(){
    try{
      const value=JSON.parse(this.storage.getItem(this.key));
      if(value&&typeof value.enabled==='boolean'&&value.jobs&&typeof value.jobs==='object'&&!Array.isArray(value.jobs))this.memory=value;
    }catch(error){/* In-memory reminders remain available without storage. */}
    return this.memory;
  }
  write(value){
    value.jobs=Object.fromEntries(Object.entries(value.jobs).slice(-200));this.memory=value;
    try{this.storage.setItem(this.key,JSON.stringify(value));}catch(error){/* No permission or full storage. */}
  }
  supported(){return window.isSecureContext&&'Notification' in window;}
  render(message){
    const enabled=this.read().enabled, supported=this.supported(), permission=supported?Notification.permission:'unsupported';
    this.toggle.textContent=enabled?'关闭系统提醒':'开启系统提醒';
    this.toggle.disabled=!supported;
    this.test.disabled=!supported||permission!=='granted';
    this.hint.textContent=message||(!supported?'此浏览器或访问地址不支持系统通知，仍会显示页内完成提示。':
      permission==='denied'?'通知权限已被拒绝，请在浏览器的网站设置中允许通知；页内提示仍可用。':
      enabled&&permission==='granted'?'已开启：每页实际完成后提醒一次。请保持网页打开；切到后台可能延迟提醒。':
      '每写完一页显示完成提示；开启后同时发送系统通知。请保持网页打开。');
  }
  async toggleEnabled(){
    if(this.read().enabled){this.write({...this.read(),enabled:false});this.render();return;}
    if(!this.supported())return;
    // This call must occur directly within the user's click gesture.
    try{
      const permission=Notification.permission==='default'?await Notification.requestPermission():Notification.permission;
      this.write({...this.read(),enabled:permission==='granted'});this.render();
    }catch(error){this.render('无法开启系统通知，请检查浏览器通知权限。');}
  }
  async show(title,options){
    if('serviceWorker' in navigator){
      if(!this.registration)this.registration=navigator.serviceWorker.register('/notification-worker.js');
      const registration=await this.registration;
      if(!registration.active){
        await new Promise((resolve,reject)=>{
          const worker=registration.installing||registration.waiting;
          if(!worker)return reject(new Error('通知服务未就绪'));
          const timer=setTimeout(()=>reject(new Error('通知服务启动超时')),8000);
          const changed=()=>{if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve();}};
          worker.addEventListener('statechange',changed);changed();
        });
      }
      await registration.showNotification(title,options);return;
    }
    const notification=new Notification(title,options);
    notification.onclick=()=>{window.focus();notification.close();};
  }
  async testNotification(){
    if(!this.supported()||Notification.permission!=='granted')return;
    try{await this.notify('纸间 · 测试提醒',{body:'通知已就绪。每写完一页会在这里提醒你。',tag:'plotter-notification-test'});this.render('测试通知已提交；若未看到，请检查系统通知设置或勿扰模式。');}
    catch(error){this.registration=null;this.render('系统通知发送失败，页内完成提示仍可用。');}
  }
  jobKey(state){return JSON.stringify([(window.PLOTTER_API_BASE||location.origin).replace(/\/$/,''),state.port||'',state.job_id]);}
  remember(state,details={}){
    if(!state.job_id)return;
    const value=this.read(),key=this.jobKey(state);
    if(value.jobs[key]?.done)return;
    const previous=value.jobs[key]||{};
    const next={...previous,...details,device:details.device||previous.device||state.port||'写字机'};
    if(JSON.stringify(previous)!==JSON.stringify(next)){value.jobs[key]=next;this.write(value);}
  }
  observe(state,details={}){
    if(!state.job_id)return;
    const key=this.jobKey(state);
    if(['running','paused','draining'].includes(state.phase)){
      this.completion.hidden=true;this.remember(state,details);return;
    }
    // Initial historical completion without an observed/started task is ignored.
    if(state.phase!=='complete'||!this.read().jobs[key]||this.inflight.has(key))return;
    const job=this.read().jobs[key], page=job.page?`第 ${job.page}${job.pages?' / '+job.pages:''} 页`:'本页';
    const text=`${job.device}：${page}${job.simulation?'模拟完成':'已写完'}，已抬笔返回原点。${job.pages&&job.page>=job.pages?'本页为文档最后一页。':'请换纸后确认原点，再开始下一页。'}`;
    if(this.completion.textContent!==text)this.completion.textContent=text;
    this.completion.hidden=false;
    if(job.done)return;
    this.inflight.add(key);
    const deliver=async()=>{
      const value=this.read();if(value.jobs[key]?.done)return;
      // Claim before awaiting browser APIs. Web Locks serializes claims across tabs.
      value.jobs[key]={...value.jobs[key],done:true};this.write(value);
      if(!value.enabled||!this.supported()||Notification.permission!=='granted')return;
      try{await this.notify(job.simulation?'纸间 · 模拟页完成':'纸间 · 本页书写完成',{body:text,tag:'plotter-page-'+key,renotify:false});}
      catch(error){this.registration=null;this.render('系统通知发送失败；本页已完成，请查看下方提示。');}
    };
    const work=navigator.locks?.request?navigator.locks.request('plotter-page-'+key,deliver):deliver();
    Promise.resolve(work).catch(()=>this.render('系统通知暂不可用；请查看页内完成提示。')).finally(()=>this.inflight.delete(key));
  }
};
