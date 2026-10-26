"""Read saved jobs/settings/logs only; never open serial or control endpoints."""
import collections
import json
import math
import pathlib
import re

root = pathlib.Path(r'C:\Users\102\plotter-web-20261014\controller-jobs')
motion = re.compile(r'G1 (?:X([-\d.]+) Y([-\d.]+)|Z([-\d.]+)) F([\d.]+)')
reports = []
for folder in sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)[:12]:
    path = folder / 'job.gcode'
    if not path.exists(): continue
    pos = [0.,0.,0.]
    drawing = collections.Counter()
    moves = collections.Counter()
    lengths = collections.Counter()
    seconds = collections.Counter()
    strokes = 0
    for line in path.read_text(encoding='ascii').splitlines():
        match = motion.fullmatch(line)
        if not match: continue
        x,y,z,feed = match.groups();feed=float(feed)
        if z is not None:
            distance=abs(float(z)-pos[2]);pos[2]=float(z)
            kind='pen';strokes+=int(pos[2]==10)
        else:
            distance=math.dist(pos[:2],(float(x),float(y)));pos[:2]=[float(x),float(y)]
            kind='drawing' if pos[2]==10 else 'travel'
            (drawing if kind=='drawing' else moves)[feed]+=1
        lengths[kind]+=distance;seconds[kind]+=distance/feed*60
    probe=folder/'probe.json'
    settings=json.loads(probe.read_text()).get('settings',[]) if probe.exists() else []
    report=dict(job_id=folder.name,drawing_feeds=dict(drawing),travel_feeds=dict(moves),strokes=strokes,
                distance_mm={k:round(v,1) for k,v in lengths.items()},
                constant_speed_seconds={k:round(v,1) for k,v in seconds.items()},
                settings=[s for s in settings if re.match(r'\$(?:11|110|111|112|120|121|122)=',s)])
    log=folder/'serial.jsonl'
    if log.exists():
        speeds=[];overrides=collections.Counter()
        with log.open(encoding='utf8') as stream:
            for line in collections.deque(stream,maxlen=4000):
                try: entry=json.loads(line)
                except ValueError: continue
                raw=bytes.fromhex(entry['hex'])
                if entry['direction']=='tx' and len(raw)==1 and 0x90<=raw[0]<=0x94: overrides[raw.hex()]+=1
                if entry['direction']=='rx':
                    match=re.search(rb'\|FS:([\d.]+),',raw)
                    if match and float(match[1])>0: speeds.append(float(match[1]))
        report['recent_status_feed']=dict(samples=len(speeds),maximum=max(speeds,default=0),
                                          median=sorted(speeds)[len(speeds)//2] if speeds else 0)
        report['recent_override_commands']=dict(overrides)
    reports.append(report)
print(json.dumps(reports,ensure_ascii=False,indent=2))
