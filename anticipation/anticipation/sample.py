"""
API functions for sampling from anticipatory infilling models.
"""

import math

import numpy as np
import torch
import torch.nn.functional as F

from tqdm import tqdm

from anticipation import ops
from anticipation.config import *
from anticipation.vocab import *


def safe_logits(logits, idx):
    logits[CONTROL_OFFSET:SPECIAL_OFFSET] = -float('inf') # don't generate controls
    logits[SPECIAL_OFFSET:] = -float('inf')               # don't generate special tokens

    # don't generate stuff in the wrong time slot
    if idx % 3 == 0:
        logits[DUR_OFFSET:DUR_OFFSET+MAX_DUR] = -float('inf')
        logits[NOTE_OFFSET:NOTE_OFFSET+MAX_NOTE] = -float('inf')
    elif idx % 3 == 1:
        logits[TIME_OFFSET:TIME_OFFSET+MAX_TIME] = -float('inf')
        logits[NOTE_OFFSET:NOTE_OFFSET+MAX_NOTE] = -float('inf')
    elif idx % 3 == 2:
        logits[TIME_OFFSET:TIME_OFFSET+MAX_TIME] = -float('inf')
        logits[DUR_OFFSET:DUR_OFFSET+MAX_DUR] = -float('inf')

    return logits


def nucleus(logits, top_p):
    # from HF implementation
    if top_p < 1.0:
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

        # Remove tokens with cumulative probability above the threshold (token with 0 are kept)
        sorted_indices_to_remove = cumulative_probs > top_p

        # Shift the indices to the right to keep also the first token above the threshold
        sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
        sorted_indices_to_remove[..., 0] = 0

        # scatter sorted tensors to original indexing
        indices_to_remove = sorted_indices_to_remove.scatter(0, sorted_indices, sorted_indices_to_remove)
        logits[indices_to_remove] = -float("inf")

    return logits


def future_logits(logits, curtime):
    """ don't sample events in the past """
    if curtime > 0:
        logits[TIME_OFFSET:TIME_OFFSET+curtime] = -float('inf')

    return logits


def instr_logits(logits, full_history):
    """ don't sample more than 16 instruments """
    instrs = ops.get_instruments(full_history)
    if len(instrs) < 15: # 16 - 1 to account for the reserved drum track
        return logits

    for instr in range(MAX_INSTR):
        if instr not in instrs:
            logits[NOTE_OFFSET+instr*MAX_PITCH:NOTE_OFFSET+(instr+1)*MAX_PITCH] = -float('inf')

    return logits


def _on_grid(ticks, step):
    """True where an absolute tick position sits on the grid {round(k*step)}."""
    return ticks == np.round(np.round(ticks / step) * step)


def grid_time_logits(logits, offset, grid, current_time=0, n_same=0):
    """Restrict onset times to the metrical grid, and favour beats / downbeats."""
    ticks = np.arange(MAX_TIME) + offset
    ok = _on_grid(ticks, grid['step'])
    if n_same >= grid.get('max_chord', 4):  # stacked enough notes here: time must move forward
        later = ok & (ticks > current_time)
        if later.any():
            ok = later
    if grid.get('avoid') is not None and len(grid['avoid']):  # complementary parts never attack with the melody
        free = ok & ~np.isin(ticks, grid['avoid'])
        if free.any():
            ok = free
    if grid.get('min_gap') and n_same >= 1:  # sparse parts: leave room between onsets (never stack notes either)
        spaced = ok & (ticks >= current_time + grid['min_gap'])
        if spaced.any():
            ok = spaced
    if grid.get('max_gap'):  # no long silences: the next onset must come soon
        soon = ok & (ticks <= current_time + grid['max_gap'])
        if soon.any():
            ok = soon
    beat = grid.get('beat', grid['step'])
    bonus = (_on_grid(ticks, beat) * grid.get('beat_boost', 0.0)
             + _on_grid(ticks, grid.get('bar', beat)) * grid.get('bar_boost', 0.0))
    seg = logits[TIME_OFFSET:TIME_OFFSET+MAX_TIME]
    ok = torch.from_numpy(ok).to(logits.device)
    new = torch.where(ok, seg + torch.from_numpy(bonus).float().to(logits.device), torch.full_like(seg, -float('inf')))
    if torch.isfinite(new).any():  # never leave the sampler with nothing to choose
        logits[TIME_OFFSET:TIME_OFFSET+MAX_TIME] = new
    return logits


def grid_dur_logits(logits, onset, grid):
    """Restrict durations so every note ends on the grid (and lasts at least one grid step)."""
    d = np.arange(MAX_DUR)
    ok = _on_grid(onset + d, grid['step'])    # the grid matters most; the rest are dropped one at a time if impossible
    longer = ok & (d >= max(int(grid['step']) - 1, int(grid.get('min_dur', 0))))
    if longer.any():
        ok = longer
    if grid.get('max_dur'):  # short, detached notes vs long, held ones
        limited = ok & (d <= grid['max_dur'])
        if limited.any():
            ok = limited
    seg = logits[DUR_OFFSET:DUR_OFFSET+MAX_DUR]
    ok = torch.from_numpy(ok).to(logits.device)
    new = torch.where(ok, seg, torch.full_like(seg, -float('inf')))
    if torch.isfinite(new).any():
        logits[DUR_OFFSET:DUR_OFFSET+MAX_DUR] = new
    return logits


def _pool_mask(pool, device):
    """Bool tensor [instrument, pitch]: which (instrument, pitch) notes the pool permits at all (the instruments and
    their playable ranges). Cached per pool, and rebuilt if the pool's instruments or ranges change."""
    ranges = pool.get('ranges', {})
    sig = (str(device), tuple(pool['allowed']), tuple(sorted((k, tuple(v)) for k, v in ranges.items())),
           tuple(pool['pitch_range']) if pool.get('pitch_range') else None)
    cache = pool.setdefault('_mask_cache', {})
    if sig not in cache:
        arr = np.zeros((MAX_INSTR, MAX_PITCH), dtype=bool)
        for instr in pool['allowed']:
            lo, hi = ranges.get(instr) or pool.get('pitch_range') or (0, MAX_PITCH - 1)
            arr[instr, lo:hi+1] = True
        cache.clear()
        cache[sig] = torch.from_numpy(arr).to(device)
    return cache[sig]


_SCALE_CACHE = {}


def _key_mask(pool, device):
    """Bool tensor [instrument, pitch]: the pitch classes of the key the part may use. Drums are exempt."""
    pcs = pool.get('pitch_classes')
    if not pcs:
        return None
    key = (str(device), tuple(sorted(pcs)))
    mask = _SCALE_CACHE.get(key)
    if mask is None:
        arr = np.ones((MAX_INSTR, MAX_PITCH), dtype=bool)
        arr[:, [p for p in range(MAX_PITCH) if p % 12 not in pcs]] = False
        arr[128] = True      # drums have no pitch: they are exempt from the scale
        mask = torch.from_numpy(arr).to(device)
        _SCALE_CACHE[key] = mask
    return mask


def _chord_bonus(pool, onset, device, weight):
    """A bonus for the pitch classes of the chord that is sounding where the note lands. The plan gives one chord
    per bar; a note that belongs to it is more likely, but the melody may still pass through other notes of the
    scale on the way - a hard restriction would leave only three pitches to choose from and the line would stall."""
    regions = pool.get('regions')
    if not regions or onset is None or weight <= 0:
        return None
    pcs = None
    for start, end, p in regions:
        if start <= onset < end:
            pcs = p
            break
    if not pcs:
        return None
    vec = torch.zeros(MAX_PITCH, device=device)
    for p in pcs:
        vec[p::12] = weight
    bonus = vec.unsqueeze(0).expand(MAX_INSTR, MAX_PITCH).clone()
    bonus[128] = 0.0       # drums have no pitch
    return bonus


def steer_instruments(logits, tokens, pool, onset=None):
    """Constrain which instrument/pitch the next note may use.

    pool = {'allowed': [...], 'ranges': {instr: (lo, hi)}, 'max_poly': {instr: n}, 'balance': float, 'free': bool}
      - only allowed instruments, and only pitches inside each instrument's playable range
      - an instrument already sounding max_poly notes at `onset` can't take another (for single-note instruments
        only a *simultaneous* onset is refused; legato overlaps are trimmed afterwards)
      - unless 'free', an instrument that has fallen behind its fair share must play next, and under-used ones are
        boosted, so that every instrument in the pool actually plays
      - 'regions' restricts the pitch classes bar by bar, so the part follows the piece's harmony
    """
    allowed = list(pool['allowed'])
    if not allowed:
        return logits
    max_poly = pool.get('max_poly', {})
    counts = {i: 0 for i in allowed}
    active = {i: 0 for i in allowed}
    for t, d, note in zip(tokens[0::3], tokens[1::3], tokens[2::3]):
        if note >= CONTROL_OFFSET:  # generated/prompt events only, not anticipated controls
            continue
        instr = (note - NOTE_OFFSET) // MAX_PITCH
        if instr not in counts:
            continue
        counts[instr] += 1
        if onset is not None and instr in max_poly:
            start, dur = t - TIME_OFFSET, d - DUR_OFFSET
            sounding = start == onset if max_poly[instr] == 1 else start <= onset < start + dur
            active[instr] += sounding

    candidates = [i for i in allowed if not (i in max_poly and active[i] >= max_poly[i])] or allowed
    if not pool.get('free'):
        total = sum(counts.values())
        # Hard rule: an instrument well behind its fair share must play next. The model's own preference for the
        # instrument it just used is far stronger than any soft bonus.
        floor = int(total / (pool.get('floor_div', 2) * len(allowed)))
        starved = [i for i in candidates if counts[i] < floor or (counts[i] == 0 and total >= len(allowed))]
        candidates = starved or candidates
    keep = torch.zeros(MAX_INSTR, dtype=torch.bool, device=logits.device)
    keep[candidates] = True
    keep_all = torch.zeros(MAX_INSTR, dtype=torch.bool, device=logits.device)
    keep_all[allowed] = True

    bonus = torch.zeros(MAX_INSTR, device=logits.device)
    if pool.get('balance') and not pool.get('free'):
        mean = sum(counts.values()) / len(counts)
        for i in candidates:
            bonus[i] = -pool['balance'] * math.log((counts[i] + 1) / (mean + 1))

    notes = logits[NOTE_OFFSET:NOTE_OFFSET+MAX_NOTE].view(MAX_INSTR, MAX_PITCH)
    base = _pool_mask(pool, logits.device)
    keymask = _key_mask(pool, logits.device)
    chord = _chord_bonus(pool, onset, logits.device, pool.get('chord_weight', 0.0))
    scored = notes + bonus[:, None]
    if chord is not None:
        scored = scored + chord
    # If the model gives no probability to anything the full rules allow, relax one rule at a time, least important
    # first: the fair share, then the scale. The playable range and the instrument list stay in force.
    strict = base & keep[:, None]
    relaxed = base & keep_all[:, None]
    tiers = [strict & keymask, relaxed & keymask, relaxed] if keymask is not None else [strict, relaxed]
    for mask in tiers:
        steered = torch.where(mask, scored, torch.full_like(scored, -float('inf')))
        if torch.isfinite(steered).any():
            logits[NOTE_OFFSET:NOTE_OFFSET+MAX_NOTE] = steered.reshape(-1)
            break
    return logits


def _target_bias(targets, onset, weight, tol, sigma):
    """Pull the melody towards a target. `targets` is a list of (tick, pitch) anchors - the motif at a phrase's
    start, the cadence at a phrase's end. The anchor nearest this onset (within `tol` ticks) is the one that speaks,
    and a note that lands between two anchors is pulled towards the line between them, so the melody follows the
    shape of the idea rather than only its exact notes. Nothing is forced: the target only makes the intended note
    more likely, and notes that land away from every anchor are left alone."""
    if not targets or onset is None or weight <= 0:
        return np.zeros(MAX_PITCH, dtype=np.float32)
    ts = sorted(targets)
    # the pitch to aim at: the line between two anchors if the note lands between them, else the nearest anchor
    pitch = None
    distance = None
    for a, b in zip(ts, ts[1:]):
        if a[0] <= onset <= b[0]:
            frac = (onset - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
            pitch = a[1] + frac * (b[1] - a[1])
            distance = 0.0          # between two anchors the shape is known, so the pull is at full strength
            break
    if pitch is None:
        best = min(ts, key=lambda tp: abs(tp[0] - onset))
        distance = abs(best[0] - onset)
        if distance > tol:
            return np.zeros(MAX_PITCH, dtype=np.float32)
        pitch = best[1]
    strength = weight * max(0.0, 1.0 - distance / tol)
    x = np.arange(MAX_PITCH)
    return (strength * np.exp(-((x - pitch) ** 2) / (2.0 * sigma * sigma))).astype(np.float32)


def melodic_bias(logits, tokens, pool, onset=None):
    """Nudge a single-line melody away from monotony, and towards what the piece is meant to say.
    pool['melodic'] = {'run', 'leap', 'repeat', 'variety'} weights (logit units; 0 = off) plus 'instrument'
    (the melody's program). Looks at the pitches the melody instrument has played so far (seed included) and
    - discourages a third step in the same direction (scalar runs) and repeated notes,
    - favours thirds-to-fifths leaps, and stepping back the other way after a big leap (a leap is answered),
    - discourages wild jumps (over a sixth), and shuffling among the same few pitches,
    - rewards reaching beyond the range used so far while that range is still narrow.
    pool['melodic']['targets'] is a list of (tick, pitch) anchors - the motif at each phrase's start, the cadence
    at each phrase's end - that the melody is pulled towards; 'target_weight', 'target_tol' and 'target_sigma'
    tune how strongly."""
    w = pool['melodic']
    instr = w['instrument']   # the melody instrument (set by the caller; the pool may hold others)
    hist = []
    for note in tokens[2::3]:
        if note < CONTROL_OFFSET and (note - NOTE_OFFSET) // MAX_PITCH == instr:
            hist.append((note - NOTE_OFFSET) % MAX_PITCH)
    if not hist:
        return logits
    last = hist[-1]
    last4 = hist[-4:]
    moves = [b - a for a, b in zip(last4, last4[1:])]   # the last (up to) three melodic moves
    seg = logits[NOTE_OFFSET + instr * MAX_PITCH: NOTE_OFFSET + (instr + 1) * MAX_PITCH]
    iv = np.arange(MAX_PITCH) - last
    mag = np.abs(iv)
    bias = np.zeros(MAX_PITCH, dtype=np.float32)

    bias -= w.get('repeat', 0) * (mag == 0) * (2.0 if moves and moves[-1] == 0 else 1.0)
    step = (mag > 0) & (mag <= 2)
    if len(moves) >= 2:
        d1, d2 = np.sign(moves[-1]), np.sign(moves[-2])
        if d1 != 0 and d1 == d2 and abs(moves[-1]) <= 2 and abs(moves[-2]) <= 2:
            same = step & (np.sign(iv) == d1)
            bias -= w.get('run', 0) * same * (1.6 if len(moves) >= 3 and np.sign(moves[-3]) == d1 else 1.0)
    leap = (mag >= 3) & (mag <= 7) & (mag != 6)
    bias += w.get('leap', 0) * leap
    bias -= w.get('leap', 0) * 1.5 * (mag > 9)
    if moves and abs(moves[-1]) >= 5:    # after a leap: step back the other way, don't leap on in the same direction
        back = step & (np.sign(iv) == -np.sign(moves[-1]))
        bias += w.get('leap', 0) * 1.2 * back
        bias -= w.get('leap', 0) * ((mag >= 3) & (np.sign(iv) == np.sign(moves[-1])))
    recent = hist[-8:]
    if len(recent) >= 6:
        pcs = set(recent)
        if len(pcs) <= 3:   # shuffling among a handful of notes
            bias -= w.get('variety', 0) * np.isin(np.arange(MAX_PITCH), list(pcs))
        far = hist[-16:]
        if max(far) - min(far) < 9:
            outside = (np.arange(MAX_PITCH) > max(far)) | (np.arange(MAX_PITCH) < min(far))
            bias += w.get('variety', 0) * 0.8 * (outside & (mag <= 7))
    bias += _target_bias(w.get('targets') or [], onset, w.get('target_weight', 0.0),
                         w.get('target_tol', 25), w.get('target_sigma', 1.6))
    seg += torch.from_numpy(bias).to(seg.device)
    return logits


def count_simultaneous(tokens, current_time):
    """How many generated notes already start exactly at `current_time`. Anticipated controls are appended to the tail
    of `tokens` as the clock nears them; they are skipped so they can't hide the notes just before them."""
    n = 0
    for t, note in zip(reversed(tokens[0::3]), reversed(tokens[2::3])):
        if note >= CONTROL_OFFSET:
            continue
        if t - TIME_OFFSET != current_time:
            break
        n += 1
    return n


def add_token(model, z, tokens, top_p, current_time, debug=False, grid=None, instruments=None):
    assert len(tokens) % 3 == 0

    history = tokens.copy()
    lookback = max(len(tokens) - 1017, 0)
    history = history[lookback:] # Markov window
    offset = ops.min_time(history, seconds=False)
    history[::3] = [tok - offset for tok in history[::3]] # relativize time in the history buffer

    new_token = []
    with torch.no_grad():
        # The Markov window shifts every event, so the cache can only be reused
        # within one event: prefill once, then feed the 2nd/3rd token incrementally.
        full = torch.tensor(z + history).unsqueeze(0).to(model.device)
        past = None
        for i in range(3):
            if past is None:
                out = model(full, use_cache=True)
            else:
                out = model(torch.tensor([[new_token[-1]]], device=model.device), past_key_values=past, use_cache=True)
            past = out.past_key_values
            logits = out.logits[0,-1].float()

            idx = full.shape[1] + i - 1
            logits = safe_logits(logits, idx)
            if i == 0:
                logits = future_logits(logits, current_time - offset)
                if grid is not None:
                    logits = grid_time_logits(logits, offset, grid, current_time, count_simultaneous(tokens, current_time))
            elif i == 1 and grid is not None:
                logits = grid_dur_logits(logits, new_token[0] - TIME_OFFSET + offset, grid)
            elif i == 2:
                logits = instr_logits(logits, tokens)
                if instruments is not None:
                    onset = new_token[0] - TIME_OFFSET + offset
                    logits = steer_instruments(logits, tokens, instruments, onset)
                    if instruments.get('melodic'):
                        logits = melodic_bias(logits, tokens, instruments, onset)
            logits = nucleus(logits, top_p)

            probs = F.softmax(logits, dim=-1)
            token = torch.multinomial(probs, 1)
            new_token.append(int(token))

    new_token[0] += offset # revert to full sequence timing
    if debug:
        print(f'  OFFSET = {offset}, LEN = {len(history)}, TIME = {tokens[::3][-5:]}')

    return new_token


def generate(model, start_time, end_time, inputs=None, controls=None, top_p=1.0, debug=False, delta=DELTA*TIME_RESOLUTION, grid=None, instruments=None):
    if inputs is None:
        inputs = []

    if controls is None:
        controls = []

    start_time = int(TIME_RESOLUTION*start_time)
    end_time = int(TIME_RESOLUTION*end_time)

    # prompt is events up to start_time
    prompt = ops.pad(ops.clip(inputs, 0, start_time, clip_duration=False, seconds=False), start_time)

    # treat events beyond start_time as controls
    future = ops.clip(inputs, start_time+1, ops.max_time(inputs, seconds=False), clip_duration=False, seconds=False)
    if debug:
        print('Future')
        ops.print_tokens(future)

    # clip controls that preceed the sequence
    controls = ops.clip(controls, DELTA, ops.max_time(controls, seconds=False), clip_duration=False, seconds=False)

    if debug:
        print('Controls')
        ops.print_tokens(controls)

    z = [ANTICIPATE] if len(controls) > 0 or len(future) > 0 else [AUTOREGRESS]
    if debug:
        print('AR Mode' if z[0] == AUTOREGRESS else 'AAR Mode')

    # interleave the controls with the events
    tokens, controls = ops.anticipate(prompt, ops.sort(controls + [CONTROL_OFFSET+token for token in future]))

    if debug:
        print('Prompt')
        ops.print_tokens(tokens)

    current_time = ops.max_time(prompt, seconds=False)
    if debug:
        print('Current time:', current_time)

    with tqdm(range(end_time-start_time)) as progress:
        if controls:
            atime, adur, anote = controls[0:3]
            anticipated_tokens = controls[3:]
            anticipated_time = atime - ATIME_OFFSET
        else:
            # nothing to anticipate
            anticipated_time = math.inf

        while True:
            while current_time >= anticipated_time - delta:
                tokens.extend([atime, adur, anote])
                if debug:
                    note = anote - ANOTE_OFFSET
                    instr = note//2**7
                    print('A', atime - ATIME_OFFSET, adur - ADUR_OFFSET, instr, note - (2**7)*instr)

                if len(anticipated_tokens) > 0:
                    atime, adur, anote = anticipated_tokens[0:3]
                    anticipated_tokens = anticipated_tokens[3:]
                    anticipated_time = atime - ATIME_OFFSET
                else:
                    # nothing more to anticipate
                    anticipated_time = math.inf

            new_token = add_token(model, z, tokens, top_p, max(start_time,current_time), grid=grid, instruments=instruments)
            new_time = new_token[0] - TIME_OFFSET
            if new_time >= end_time:
                break

            if debug:
                new_note = new_token[2] - NOTE_OFFSET
                new_instr = new_note//2**7
                new_pitch = new_note - (2**7)*new_instr
                print('C', new_time, new_token[1] - DUR_OFFSET, new_instr, new_pitch)

            tokens.extend(new_token)
            dt = new_time - current_time
            assert dt >= 0
            current_time = new_time
            progress.update(dt)

    events, _ = ops.split(tokens)
    return ops.sort(ops.unpad(events) + future)


def generate_ar(model, start_time, end_time, inputs=None, controls=None, top_p=1.0, debug=False, delta=DELTA*TIME_RESOLUTION):
    if inputs is None:
        inputs = []

    if controls is None:
        controls = []
    else:
        # treat controls as ordinary tokens
        controls = [token-CONTROL_OFFSET for token in controls]

    start_time = int(TIME_RESOLUTION*start_time)
    end_time = int(TIME_RESOLUTION*end_time)

    inputs = ops.sort(inputs + controls)

    # prompt is events up to start_time
    prompt = ops.pad(ops.clip(inputs, 0, start_time, clip_duration=False, seconds=False), start_time)
    if debug:
        print('Prompt')
        ops.print_tokens(prompt)

    # treat events beyond start_time as controls
    controls = ops.clip(inputs, start_time+1, ops.max_time(inputs, seconds=False), clip_duration=False, seconds=False)
    if debug:
        print('Future')
        ops.print_tokens(controls)

    z = [AUTOREGRESS]
    if debug:
        print('AR Mode')

    current_time = ops.max_time(prompt, seconds=False)
    if debug:
        print('Current time:', current_time)

    tokens = prompt
    with tqdm(range(end_time-start_time)) as progress:
        if controls:
            atime, adur, anote = controls[0:3]
            anticipated_tokens = controls[3:]
            anticipated_time = atime - TIME_OFFSET
        else:
            # nothing to anticipate
            anticipated_time = math.inf

        while True:
            new_token = add_token(model, z, tokens, top_p, max(start_time,current_time))
            new_time = new_token[0] - TIME_OFFSET
            if new_time >= end_time:
                break

            dt = new_time - current_time
            assert dt >= 0
            current_time = new_time

            # backfill anything that should have come before the new token
            while current_time >= anticipated_time:
                tokens.extend([atime, adur, anote])
                if debug:
                    note = anote - NOTE_OFFSET
                    instr = note//2**7
                    print('A', atime - TIME_OFFSET, adur - DUR_OFFSET, instr, note - (2**7)*instr)

                if len(anticipated_tokens) > 0:
                    atime, adur, anote = anticipated_tokens[0:3]
                    anticipated_tokens = anticipated_tokens[3:]
                    anticipated_time = atime - TIME_OFFSET
                else:
                    # nothing more to anticipate
                    anticipated_time = math.inf

            if debug:
                new_note = new_token[2] - NOTE_OFFSET
                new_instr = new_note//2**7
                new_pitch = new_note - (2**7)*new_instr
                print('C', new_time, new_token[1] - DUR_OFFSET, new_instr, new_pitch)

            tokens.extend(new_token)
            progress.update(dt)

    if anticipated_time != math.inf:
        tokens.extend([atime, adur, anote])

    return ops.sort(ops.unpad(tokens) + controls)
