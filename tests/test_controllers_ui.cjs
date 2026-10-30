const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),jobs=path.resolve(root,'tests/.testdata/controllers-ui-'+process.pid);
const script=`
import sys,pathlib
from backend.server import Application,create_server
from tests.test_controllers import Controller
fonts=pathlib.Path(sys.argv[1]);jobs=pathlib.Path(sys.argv[2])
a=Application(fonts,jobs/'windows',controller=Controller('windows'))
b=Application(fonts,jobs/'pi',controller=Controller('raspberrypi'),shared_fonts=a)
a.machine.state.update(connected=True,phase='running',operation_type='writing',job_id='f'*32,port='USB0',mode='simulation',acknowledged=2,total=10,status='<Run|MPos:0,0,0>')
b.default_control=dict(controller_type='grbl',pen_up=0,pen_down=13.5,pen_feed=100)
b.machine.state.update(mode='simulation',status='',calibrated=False,startup_mode='paper-contact',motion_revision=0)
server=create_server(a,port=0,controllers={'windows':dict(name='Windows 写字机',app=a),'raspberrypi':dict(name='树莓派写字机',app=b,contact_startup=True)})
print(server.server_port,flush=True)
server.serve_forever()
`;
const python=spawn('python',['-u','-c',script,path.join(root,'tests/fixtures'),jobs],{cwd:root,windowsHide:true});
let browser;
(async()=>{
 const port=await new Promise((resolve,reject)=>{let out='';python.stdout.on('data',d=>{out+=d;if(/^\d+\r?\n/.test(out))resolve(Number(out.trim()));});python.on('error',reject);python.on('exit',c=>reject(Error('server exited '+c)));});
 browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1280,height:1100}}),errors=[],operations=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&/^\/api\/(connect|disconnect|prepare|start|pause|resume|stop|pen|jog)$/.test(new URL(r.url()).pathname))operations.push([new URL(r.url()).pathname,r.headers()['x-plotter-controller']]);});
 await page.goto('http://127.0.0.1:'+port);
 await page.waitForFunction(()=>window.sharedWorkspace?.ready&&!busy);
 assert.equal(await page.locator('[data-controller]').count(),2);
 await page.click('[data-controller="raspberrypi"]');
 await page.waitForFunction(()=>controllerId==='raspberrypi'&&window.sharedWorkspace.ready&&!busy);
 assert.equal(await page.locator('#contactStartup').isVisible(),true);
 assert.match(await page.locator('#contactStartup').innerText(),/断电换笔/);
 assert.match(await page.locator('#contactStartup').innerText(),/不要随意调整弹簧控制/);
 assert.equal(await page.inputValue('#penDownZ'),'13.5');
 assert.equal(await page.locator('#prepareMachine').isDisabled(),true);
 assert.equal(await page.locator('#start').isDisabled(),true);
 await page.click('#connect');await page.waitForFunction(()=>state.connected&&!busy);
 assert.deepEqual(operations,[['/api/connect','raspberrypi']]);
 assert.equal(await page.locator('#prepareMachine').isDisabled(),true);
 await page.check('#confirmPowerCycle');await page.check('#confirmPaperContact');
 // Stub status is made Idle as if the controller answered its read-only poll.
 await page.evaluate(()=>{state.status='<Idle|MPos:0,0,0>';renderState();});
 assert.equal(await page.locator('#prepareMachine').isEnabled(),true);
 await page.locator('.machine').screenshot({path:path.join(root,'docs/two-controllers-desktop.png')});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 await page.locator('.machine').screenshot({path:path.join(root,'docs/two-controllers-mobile.png')});
 await page.click('[data-controller="windows"]');
 await page.waitForFunction(()=>controllerId==='windows'&&window.sharedWorkspace.ready&&!busy);
 assert.equal(await page.locator('#contactStartup').isVisible(),false);
 assert.equal(await page.evaluate(()=>state.phase),'running');
 assert.deepEqual(operations,[['/api/connect','raspberrypi']]);
 assert.deepEqual(errors,[]);
 console.log('PASS: controller switch, background task preserved, scoped connect, power/spring instructions, calibration confirmation gates, desktop/mobile layout');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();python.kill();await new Promise(resolve=>python.exitCode!==null?resolve():python.once('exit',resolve));if(path.dirname(jobs)===path.resolve(root,'tests/.testdata'))fs.rmSync(jobs,{recursive:true,force:true});});
