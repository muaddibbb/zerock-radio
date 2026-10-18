#!/usr/bin/env python3
"""Render a preview of the transition between two songs, using the SAME Liquidsoap transition code
(rocky_transition.liq), the same per-song gain and the same limiter that Rocky uses on air.

    python3 rocky_transition_preview.py <song A> <song B> <out.mp3>

Only the last PREVIEW_SECS of A and the first PREVIEW_SECS of B are decoded, so a preview takes a few
seconds. Per-song analysis tags come from the analysis cache (rocky_transitions.py) with any manual
overrides applied; a song that has not been analysed yet is analysed on the spot.
Returns / prints the transition decision parsed from Liquidsoap's own log.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import rocky_transitions as T
    import rocky_loudness as L
except ImportError:                      # run from another directory
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rocky_transitions as T
    import rocky_loudness as L

RADIO_DIR = T.RADIO_DIR
PREVIEW_SECS = 30
LIQ_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rocky_transition.liq')


def _cut(src, dst, tail):
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
    cmd += (['-sseof', f'-{PREVIEW_SECS}'] if tail else [])
    cmd += ['-i', src]
    cmd += ([] if tail else ['-t', str(PREVIEW_SECS)])
    cmd += ['-vn', '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', dst]
    subprocess.run(cmd, check=True, timeout=120)


def _duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                       capture_output=True, text=True, timeout=30)
    return float(r.stdout.strip())


def song_info(path, cache=None, overrides=None):
    """(analysis incl. overrides, loudness gain dB or None); analyses/measures on the fly when missing."""
    tc = T.load_cache() if cache is None else cache
    a = T.effective(path, tc, overrides)
    if a is None:
        T.scan([path], workers=1, log=lambda m: None)
        a = T.effective(path, T.load_cache(), overrides)
    lc = L.load_cache()
    e = lc.get(path)
    if not (e and e.get('lufs') is not None):
        m = L.measure(path)
        if m:
            e = {'lufs': m[0], 'peak': m[1]}
    gain = L.gain_db(e) if e and e.get('lufs') is not None else None
    return a, gain


def _tags(path, a, gain, overrides, cache, cut_tags_only=True):
    parts = []
    if gain is not None:
        parts.append(f'liq_amplify="{gain:.2f} dB"')
    t = T.tags_for(path, cache, overrides)
    if t:
        parts.append(t)
    return ','.join(parts)


def render(path_a, path_b, out_mp3, overrides=None, pair_override=None):
    ov = T.load_overrides() if overrides is None else overrides
    cache = T.load_cache()
    for p in (path_a, path_b):
        if not T.entry_valid(cache.get(p), p):
            T.scan([p], workers=1, log=lambda m: None)
            cache = T.load_cache()
    a_info, a_gain = song_info(path_a, cache, ov)
    b_info, b_gain = song_info(path_b, cache, ov)
    work = tempfile.mkdtemp(prefix='trprev_')
    try:
        wa, wb = os.path.join(work, 'a.wav'), os.path.join(work, 'b.wav')
        _cut(path_a, wa, True)
        _cut(path_b, wb, False)
        len_a = _duration(wa)
        tags_a = _tags(path_a, a_info, a_gain, ov, cache)
        tags_b = _tags(path_b, b_info, b_gain, ov, cache)
        pl = os.path.join(work, 'pl.m3u')
        with open(pl, 'w', encoding='utf-8') as f:
            f.write(f'annotate:{tags_a}:{wa}\nannotate:{tags_b}:{wb}\n')
        out_wav = os.path.join(work, 'out.wav')
        script = os.path.join(work, 'prev.liq')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(f'''
settings.log.stdout := true
settings.log.file := false
settings.log.level := 3
settings.init.allow_root := true
%include "{LIQ_FILE}"
src = playlist(mode="normal", loop=false, reload_mode="never", "{pl}")
src = amplify(1., src)
src = limit(attack=2., release=120., threshold=-3., src)
x = cross(duration=5., rocky_smart_xfade, src)
x = clock(sync="none", x)
output.file(%wav, "{out_wav}", fallible=true, on_stop=fun() -> shutdown(), x)
''')
        try:
            r = subprocess.run(['liquidsoap', script], capture_output=True, text=True, timeout=int(os.environ.get('TRPREV_TIMEOUT', '150')))
            log = (r.stdout or '') + (r.stderr or '')
        except subprocess.TimeoutExpired as te:
            log = ((te.stdout or b'').decode('utf-8', 'replace') if isinstance(te.stdout, bytes) else (te.stdout or '')) + \
                  ((te.stderr or b'').decode('utf-8', 'replace') if isinstance(te.stderr, bytes) else (te.stderr or ''))
            if os.environ.get('TRPREV_DEBUG'):
                print(log[-3000:])
            raise RuntimeError('render timed out: ' + log[-1200:])
        m = re.findall(r'\[transition:\d\] (.*)', log)
        decision = m[-1] if m else None
        if not os.path.exists(out_wav) or os.path.getsize(out_wav) < 100000:
            raise RuntimeError('render failed: ' + log[-800:])
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', out_wav, '-c:a', 'libmp3lame', '-b:a', '128k', out_mp3],
                       check=True, timeout=120)
        total = _duration(out_mp3)
        window = float(T.cross_window(a_info))
        return {'decision': decision, 'a': a_info, 'b': b_info, 'a_gain_db': a_gain, 'b_gain_db': b_gain,
                'len_a_cut': round(len_a, 2), 'total': round(total, 2), 'window': window,
                'transition_at': round(max(0.0, len_a - window), 2), 'out': out_mp3, 'log': log[-20000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    res = render(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps({k: v for k, v in res.items() if k != 'log'}, ensure_ascii=False, indent=1))
