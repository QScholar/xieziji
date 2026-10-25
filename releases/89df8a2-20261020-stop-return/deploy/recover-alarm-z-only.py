import datetime,json,pathlib,re,time,urllib.request
import serial

opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
audit=pathlib.Path(r'C:\Users\102\plotter-web-20261014\deploy\services')/('alarm-z-recovery-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
def record(direction,value):
    with audit.open('a',encoding='utf8') as stream:
        stream.write(json.dumps({'time':time.time(),'direction':direction,'value':value},ensure_ascii=True)+'\n')
def api(path,data=None):
    body=None if data is None else json.dumps(data).encode('ascii')
    request=urllib.request.Request('http://127.0.0.1:8080'+path,data=body,headers={'Content-Type':'application/json'})
    with opener.open(request,timeout=15) as response:return json.load(response)
def position(report):
    match=re.search(r'(?:\||<)MPos:([-0-9.]+),([-0-9.]+),([-0-9.]+)',report)
    if not match:raise RuntimeError('Missing machine position: '+report)
    return tuple(float(x) for x in match.groups())
def read():
    line=device.readline().decode('ascii',errors='strict').strip()
    if line:record('rx',line)
    return line
def write(raw):
    allowed={b'?',b'$I\n',b'$G\n',b'$#\n',b'$X\n',b'$J=G21 G90 G53 Z0 F600\n'}
    if raw not in allowed:raise RuntimeError('Unauthorized recovery command')
    record('tx',raw.decode('ascii'))
    device.write(raw);device.flush()
def status():
    write(b'?');deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        line=read()
        if line.startswith('<'):return line
        if line.startswith(('ALARM:','error:')):raise RuntimeError(line)
    raise TimeoutError('Status query timeout')
def command(raw):
    write(raw);result=[];deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        line=read()
        if not line:continue
        result.append(line)
        if line=='ok':return result
        if line.startswith(('ALARM:','error:')):raise RuntimeError(line)
    raise TimeoutError('Command acknowledgement timeout')

before=api('/api/state')
if before['connected'] or before['phase']!='disconnected':raise RuntimeError('Application must be disconnected and inactive')
report={'api_before':before,'audit':str(audit)}
device=serial.Serial(port=None,baudrate=115200,timeout=.2,write_timeout=3)
device.dtr=False;device.rts=False;device.port='COM5'
try:
    with device:
        time.sleep(.3)
        initial=status()
        if not initial.startswith(('<Alarm|','<Idle|')):raise RuntimeError('Unexpected device state: '+initial)
        start=position(initial)
        if not -.01<=start[2]<=10.01:raise RuntimeError('Z position outside verified pen range')
        info=command(b'$I\n')
        if not any('[VER:1.3a.20221114:' in line for line in info):raise RuntimeError('Unverified firmware')
        offsets={}
        for line in command(b'$#\n'):
            match=re.fullmatch(r'\[(G54|G92|TLO):([0-9.,-]+)\]',line)
            if match:offsets[match[1]]=[float(x) for x in match[2].split(',')]
        if any(len(offsets.get(k,[]))!=n for k,n in [('G54',3),('G92',3),('TLO',1)]):raise RuntimeError('Missing coordinate offsets')
        if any(abs(v)>.01 for v in [offsets['G54'][2],offsets['G92'][2],offsets['TLO'][0]]):raise RuntimeError('Unexpected Z/tool offset')
        if initial.startswith('<Alarm|'):command(b'$X\n')
        unlocked=status()
        if not unlocked.startswith('<Idle|'):raise RuntimeError('Device did not become Idle: '+unlocked)
        if any(abs(a-b)>.01 for a,b in zip(position(unlocked),start)):raise RuntimeError('Position changed before Z move')
        if abs(start[2])>.01:command(b'$J=G21 G90 G53 Z0 F600\n')
        deadline=time.monotonic()+30
        while True:
            final=status()
            if final.startswith('<Idle|'):break
            if not final.startswith(('<Jog|','<Run|')):raise RuntimeError('Unexpected lifting state: '+final)
            if time.monotonic()>deadline:raise TimeoutError('Pen lift completion timeout')
            time.sleep(.1)
        end=position(final)
        if abs(end[2])>.01 or any(abs(a-b)>.01 for a,b in zip(end[:2],start[:2])):raise RuntimeError('Final Z or unchanged XY verification failed')
        report.update(before_position=start,after_position=end,device_status=final)
except BaseException as error:
    record('failure',str(error))
    raise
report['connected']=api('/api/connect',{'port':'COM5'})
report['checked']=api('/api/check',{})
print(json.dumps(report,ensure_ascii=True))
