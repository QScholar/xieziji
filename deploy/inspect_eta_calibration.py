"""Compare a standalone estimator with the API using read-only state samples."""
import json
import pathlib
import sys
import time
import urllib.request
root=pathlib.Path(r'C:\Users\102\plotter-web-20261014')
sys.path.insert(0,str(root/'releases'/'89df8a2-20261020-live-speed'))
from backend.eta import TaskETA
estimator=TaskETA(root/'backend-jobs')
for i in range(3):
    state=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/state'))
    own=estimator.decorate(state)['eta']
    print(json.dumps(dict(ack=state['acknowledged'],api=state['eta'],standalone=own,
                         window_time=estimator.window_time,window_work=estimator.window_work,last=estimator.last)),flush=True)
    if i<2:time.sleep(5)
