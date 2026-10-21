import sys; sys.path.insert(0, "/Users/Shared/music-ai")
import text2music as t, variety, json, itertools
seeds = [[(67,0,1),(69,1,1),(71,2,1),(72,3,1)],            # rising scale
         [(67,0,1),(71,1,.5),(74,1.5,.5),(71,2,1),(69,3,1)],  # chord tones
         [(74,0,.5),(72,.5,.5),(71,1,1),(69,2,1),(67,3,1)],   # falling
         [(67,0,1),(67,1,.5),(69,1.5,.5),(71,2,2)]]           # repeated-note start
cyc = itertools.cycle(seeds)
t.compose = lambda prompt, bars, sig, level, llm, log, bpm=None, key=None: (next(cyc), 90, [], "G major")
for shape in (0, 1, 2):
    t.seed_everything(1)   # same random stream for every setting, so the runs are comparable and repeatable
    res = [c["notes"] for c in t.melody_candidates("a tune in G major", 8, "4/4", 3, "7B", 0, "small", 24, "hybrid", lambda s: None, 90, True, (7, "major"), 1, shape)]
    print(variety.line(f"hybrid shape={shape}", variety.mean_stats(res)), "n=", len(res), flush=True)
    json.dump(res, open(f"{sys.argv[1]}_{shape}.json", "w"))
