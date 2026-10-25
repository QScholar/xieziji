'use strict';
// No fetch handler or cache: installing reminders never caches machine state.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil((async()=>{
    const pages=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const page=pages.find(client=>new URL(client.url).origin===self.location.origin);
    if(page){await page.focus();return;}
    await self.clients.openWindow('/');
  })());
});
