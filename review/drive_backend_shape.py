import json, os, shutil, subprocess, sys
OUT = '/Users/Shared/music-ai/output'
BEFORE = set(os.listdir(OUT))   # session folders this script creates are removed at the end
sys.path.insert(0,'/Users/Shared/music-ai'); import variety
p = subprocess.Popen(['/Users/Shared/music-ai/.venv/bin/python','-u','backend.py'], cwd='/Users/Shared/music-ai', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def send(o): p.stdin.write(json.dumps(o)+'\n'); p.stdin.flush()
def until(stops):
    evs=[]
    while True:
        e=json.loads(p.stdout.readline()); evs.append(e)
        if e['type'] in stops: return evs
until({'ready'})
for engine in ('theory','hybrid'):
    for shape in (0,2):
        send({'cmd':'melodies','prompt':'a hopeful tune in G major','bars':8,'program':73,'size':'small','sig':'4/4','complexity':3,'llm':'7B','count':4,'engine':engine,'bpm':96,'resolve':True,'phrasing':1,'shape':shape,'key':[7,'major']})
        evs=until({'melodies_done','error'})
        err=[e for e in evs if e['type']=='error']
        if err: print(engine,shape,'ERROR',err[0]); continue
        c=[e for e in evs if e['type']=='candidate']
        mel=[[(n[1],n[2],n[3]-n[2]) for n in e['notes']] for e in c]
        print(variety.line(f'{engine} shape={shape} (via backend)', variety.mean_stats(mel)), len(c))
p.stdin.close(); p.wait(timeout=30)
for d in set(os.listdir(OUT)) - BEFORE:
    if d != '.tmp': shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)
