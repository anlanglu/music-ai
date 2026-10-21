import sys, random, statistics, collections, math; sys.path.insert(0, '/Users/Shared/music-ai')
import text2music as t, composer
bad = collections.Counter(); n = 0; examples = {}
stats = collections.defaultdict(list)
for sig in ('4/4','3/4','6/8','2/4','5/4','7/8','12/8'):
    bars_set = (8, 4, 16, 6, 2, 1, 5)
    for level in (1,2,3,4,5):
        for key in ((0,'major'),(9,'minor'),(2,'major'),(7,'minor'),(10,'major'),(6,'minor')):
            for bars in bars_set if level == 3 else (8,):
                for seed in range(3 if level == 3 else 2):
                    n += 1; rng = random.Random(seed*131 + level)
                    notes, info = composer.compose(bars, sig, key, level, rng, 50, 90)
                    notes, _ = t.conform_melody(notes, bars, sig)
                    notes = t.resolve_ending(notes, bars, sig, key)
                    total = bars * t.bar_beats(sig); pcs = t.scale_pcs(key)
                    tag = f'{sig} L{level} {t.key_label(key)} {bars}b s{seed}'
                    def flag(k): bad[k] += 1; examples.setdefault(k, tag)
                    if not notes: flag('empty'); continue
                    if any(notes[i][1] + notes[i][2] > notes[i+1][1] + 1e-6 for i in range(len(notes)-1)): flag('overlap')
                    if any(p % 12 not in pcs for p,_,_ in notes): flag('out of key')
                    if any(not 50 <= p <= 90 for p,_,_ in notes): flag('out of range')
                    if any(abs(s*4-round(s*4))>1e-6 or abs(d*4-round(d*4))>1e-6 or d < 0.25 for _,s,d in notes): flag('off grid')
                    if notes[-1][1] + notes[-1][2] < total - 1e-6: flag('does not fill bars')
                    if notes[-1][1] + notes[-1][2] > total + 1e-6: flag('runs past the end')
                    if notes[-1][0] % 12 != key[0]: flag('last note not tonic')
                    if bars == 8 and sig == '4/4':
                        p = [x[0] for x in notes]; d = [round(x[2]*4) for x in notes]; iv = [b-a for a,b in zip(p,p[1:])]; c = collections.Counter(d)
                        tri = [tuple(iv[i:i+3]) for i in range(len(iv)-2)]
                        stats[level].append((len(notes), len(c), max(c.values())/len(d), len(set(iv)), max(p)-min(p), (len(tri)-len(set(tri)))/max(len(tri),1), len({x[1] % 1 for x in notes})))
print(f'{n} melodies generated; problems found:', dict(bad) if bad else 'NONE')
for k,v in examples.items(): print('   first', k, '->', v)
print('\n4/4, 8 bars, by complexity level (mean over', len(stats[1]), 'melodies each):')
print('level | notes | distinct lengths | top-length share | distinct intervals | pitch span | motif repetition | distinct beat offsets')
for l in (1,2,3,4,5):
    m = lambda i: statistics.mean(x[i] for x in stats[l])
    print(f'  {l}   | {m(0):5.1f} | {m(1):16.1f} | {m(2):15.0%} | {m(3):18.1f} | {m(4):10.1f} | {m(5):16.0%} | {m(6):5.1f}')
