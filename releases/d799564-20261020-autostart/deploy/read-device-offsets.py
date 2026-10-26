import json,time,urllib.request
import serial

opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(path,data=None):
    body=None if data is None else json.dumps(data).encode('ascii')
    request=urllib.request.Request('http://127.0.0.1:8080'+path,data=body,headers={'Content-Type':'application/json'})
    with opener.open(request,timeout=15) as response:return json.load(response)

before=api('/api/state')
assert before['connected'] and before['phase']=='idle' and before['pen_top'] is True, 'Device must be idle with pen raised'
port=before['port']
api('/api/disconnect',{})
report={'before':before,'queries':{}}
try:
    device=serial.Serial(port=None,baudrate=115200,timeout=.2,write_timeout=3)
    device.dtr=False;device.rts=False;device.port=port
    with device:
        time.sleep(.5)
        device.reset_input_buffer()
        for command in (b'?',b'$G\n',b'$#\n'):
            device.write(command);device.flush()
            lines=[];deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                line=device.readline().decode('ascii',errors='replace').strip()
                if not line:continue
                lines.append(line)
                if command==b'?' and line.startswith('<'):
                    assert line.startswith('<Idle|'), 'Machine is not Idle'
                    break
                if line=='ok':break
                if line.startswith(('error:','ALARM:')):raise RuntimeError(line)
            else:raise RuntimeError('Read-only query timeout')
            report['queries'][command.decode().strip()]=lines
finally:
    report['after']=api('/api/connect',{'port':port})
print(json.dumps(report,ensure_ascii=True))
