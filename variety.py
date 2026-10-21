"""How varied is a melody's pitch movement? Used for measuring and for scoring melody candidates.

All numbers are on a monophonic melody [(pitch, start, dur)] in time order."""


def stats(notes):
    ps = [n[0] for n in sorted(notes, key=lambda n: n[1])]
    ivs = [b - a for a, b in zip(ps, ps[1:])]
    if len(ivs) < 3:
        return {"steps": 0.0, "leaps": 0.0, "repeats": 0.0, "scalar": 1.0, "max_run": 0, "turns": 0.0,
                "kinds": 0, "range": 0}
    n = len(ivs)
    moves = [(i > 0) - (i < 0) for i in ivs]
    # a scalar run: three or more consecutive stepwise moves in one direction
    in_run = [False] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and moves[j + 1] == moves[i] and moves[i] != 0 and abs(ivs[j + 1]) <= 2 and abs(ivs[i]) <= 2:
            j += 1
        if moves[i] != 0 and abs(ivs[i]) <= 2 and j - i + 1 >= 3:
            for k in range(i, j + 1):
                in_run[k] = True
        i = j + 1
    run, best = 1, 1
    for a, b in zip(moves, moves[1:]):
        run = run + 1 if a == b and a != 0 else 1
        best = max(best, run)
    turns = sum(1 for a, b in zip(moves, moves[1:]) if a and b and a != b) / max(n - 1, 1)
    return {
        "steps": sum(1 for v in ivs if 0 < abs(v) <= 2) / n,
        "leaps": sum(1 for v in ivs if abs(v) >= 5) / n,
        "repeats": sum(1 for v in ivs if v == 0) / n,
        "scalar": sum(in_run) / n,
        "max_run": best,
        "turns": turns,
        "kinds": len({abs(v) for v in ivs}),
        "range": max(ps) - min(ps),
    }


def mean_stats(melodies):
    rows = [stats(m) for m in melodies]
    return {k: sum(r[k] for r in rows) / len(rows) for k in rows[0]}


def line(label, s):
    return (f"{label}: steps {s['steps']:.0%} | leaps(>=5st) {s['leaps']:.0%} | repeats {s['repeats']:.0%} | "
            f"in scalar runs {s['scalar']:.0%} | longest one-way run {s['max_run']:.1f} | direction turns {s['turns']:.0%} | "
            f"distinct intervals {s['kinds']:.1f} | range {s['range']:.0f}")
