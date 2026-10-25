"""Measure existing serial command timing, not physical pen contact timing."""
import collections
import json
import pathlib
import statistics
import urllib.request

state=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/state'))
path=pathlib.Path(r'C:\Users\102\plotter-web-20261014\controller-jobs')/state['job_id']/'serial.jsonl'
with path.open(encoding='utf8') as stream:
    records=[]
    for line in collections.deque(stream,maxlen=40000):
        try: records.append(json.loads(line))
        except ValueError: pass
pending=None
acknowledged=None
draw_last=None
down=False
in_gap=False
stroke_intervals=[]
post_ack=[]
ack_wait=[]
queries=0
for event in records:
    raw=bytes.fromhex(event['hex']).decode('ascii',errors='replace').strip()
    stamp=event['time']
    if event['direction']=='rx' and raw=='ok' and pending:
        ack_wait.append(stamp-pending[1]);acknowledged=stamp;pending=None
    if event['direction']!='tx': continue
    if raw=='?': queries+=1
    if not raw.startswith('G1 '): continue
    if acknowledged is not None:
        post_ack.append(stamp-acknowledged);acknowledged=None
    pending=(raw,stamp)
    if raw.startswith('G1 Z0 '):
        if down and draw_last is not None: in_gap=True
        down=False
    elif raw.startswith('G1 Z10 '): down=True
    elif raw.startswith('G1 X') and down:
        if in_gap and draw_last is not None:
            stroke_intervals.append(stamp-draw_last);in_gap=False
        draw_last=stamp

def summary(values):
    values=sorted(v*1000 for v in values)
    return dict(samples=len(values),median_ms=round(statistics.median(values),2),
                p90_ms=round(values[int((len(values)-1)*.9)],2),maximum_ms=round(values[-1],2)) if values else None

print(json.dumps(dict(job_id=state['job_id'],phase=state['phase'],
    host_after_ack_until_next_motion=summary(post_ack),command_ack_wait=summary(ack_wait),
    last_drawing_command_until_next_stroke_drawing_command=summary(stroke_intervals),
    note='Command timestamps; these are not measured pen contact/lift completion times.'),indent=2))
