"""Web routes for the Rocky smart-transitions review page (/transitions).

register(app, radio_dir, rocky_loudness_module) adds the page and its JSON API to a Flask app.
Kept in its own file so the page can be tested without starting the whole radio app.
"""
import json
import os
import random
import re
import secrets
import subprocess
import sys as _sys
import threading
import time

from flask import jsonify, render_template, request, send_file

import rocky_transitions as _rtr


def register(app, RADIO_DIR, _rocky_loudness=None):
    _TR_PREVIEW_DIR = '/tmp/rocky_trans_prev'
    _tr_preview_lock = threading.Lock()


    def _tr_song_set():
        return set(_rtr.all_tracks()) if _rtr else set()


    def _tr_song_name(path):
        return os.path.splitext(os.path.basename(path))[0]


    def _tr_enabled():
        return os.path.exists(f"{RADIO_DIR}/rocky_transitions.enabled")


    @app.route('/transitions')
    def transitions_page():
        return render_template('transitions.html')


    @app.route('/api/transitions/status')
    def api_transitions_status():
        if not _rtr:
            return jsonify({'error': 'module unavailable'}), 500
        cache = _rtr.load_cache()
        tracks = _rtr.all_tracks()
        ok = [cache[t] for t in tracks if t in cache and not cache[t].get('failed') and _rtr.entry_valid(cache[t], t)]
        from collections import Counter
        low = [t for t in tracks if t in cache and not cache[t].get('failed') and _rtr.entry_valid(cache[t], t)
               and min(cache[t].get('oe_conf', 0), cache[t].get('ie_conf', 0)) < 0.55]
        return jsonify({
            'enabled': _tr_enabled(), 'songs': len(tracks), 'analysed': len(ok),
            'endings': dict(Counter(e['oe'] for e in ok)), 'openings': dict(Counter(e['ie'] for e in ok)),
            'low_confidence': len(low), 'overrides': len(_rtr.load_overrides()),
        })


    @app.route('/api/transitions/songs')
    def api_transitions_songs():
        """Search the Rocky library by name (substring, case-insensitive)."""
        if not _rtr:
            return jsonify([])
        q = (request.args.get('q') or '').strip().lower()
        only_low = request.args.get('low') == '1'
        cache = _rtr.load_cache()
        out = []
        for t in _rtr.all_tracks():
            nm = _tr_song_name(t)
            if q and q not in nm.lower():
                continue
            e = cache.get(t)
            ok = bool(e and not e.get('failed') and _rtr.entry_valid(e, t))
            conf = min(e.get('oe_conf', 0), e.get('ie_conf', 0)) if ok else None
            if only_low and not (ok and conf < 0.55):
                continue
            out.append({'path': t, 'name': nm, 'oe': e.get('oe') if ok else None, 'ie': e.get('ie') if ok else None, 'conf': conf})
            if len(out) >= 60:
                break
        return jsonify(out)


    @app.route('/api/transitions/song')
    def api_transitions_song():
        if not _rtr:
            return jsonify({'error': 'module unavailable'}), 500
        path = request.args.get('path', '')
        if path not in _tr_song_set():
            return jsonify({'error': 'unknown song'}), 404
        cache = _rtr.load_cache()
        ov = _rtr.load_overrides()
        eff = _rtr.effective(path, cache, ov)
        return jsonify({'path': path, 'name': _tr_song_name(path), 'analysis': eff, 'overrides': ov.get(path) or {}})


    @app.route('/api/transitions/random-pair')
    def api_transitions_random_pair():
        tr = list(_tr_song_set())
        if len(tr) < 2:
            return jsonify({'error': 'no songs'}), 404
        a, b = random.sample(tr, 2)
        return jsonify({'a': {'path': a, 'name': _tr_song_name(a)}, 'b': {'path': b, 'name': _tr_song_name(b)}})


    @app.route('/api/transitions/override', methods=['POST', 'DELETE'])
    def api_transitions_override():
        """Manual per-song adjustments (cue points, forced classes, gain curves). Applies to every pair the song is in."""
        if not _rtr:
            return jsonify({'error': 'module unavailable'}), 500
        d = request.get_json(force=True, silent=True) or {}
        path = d.get('path') or request.args.get('path', '')
        if path not in _tr_song_set():
            return jsonify({'error': 'unknown song'}), 404
        ov = _rtr.load_overrides()
        if request.method == 'DELETE':
            ov.pop(path, None)
        else:
            clean = {}
            for k in _rtr.OVERRIDE_KEYS:
                if k not in d or d[k] in (None, ''):
                    continue
                v = d[k]
                if k in ('oe',) and v not in ('fade', 'decay', 'abrupt', 'unknown'):
                    return jsonify({'error': f'bad {k}'}), 400
                if k in ('ie',) and v not in ('hit', 'full', 'fade_in', 'quiet_intro', 'single_note', 'unknown'):
                    return jsonify({'error': f'bad {k}'}), 400
                if k in ('out_curve', 'in_curve') and v not in ('none', 'lin', 'sin', 'exp', 'log'):
                    return jsonify({'error': f'bad {k}'}), 400
                if k in ('out_cue', 'in_cue', 'out_fade', 'in_fade'):
                    try:
                        v = round(float(v), 2)
                    except (TypeError, ValueError):
                        return jsonify({'error': f'bad {k}'}), 400
                    if not (0 <= v <= 30):
                        return jsonify({'error': f'{k} must be 0-30 s'}), 400
                if k == 'no_overlap':
                    v = bool(v)
                clean[k] = v
            if clean:
                ov[path] = clean
            else:
                ov.pop(path, None)
        _rtr.save_overrides(ov)
        try:
            if _rocky_loudness is not None:
                _rocky_loudness.write_all()          # refresh the Liquidsoap playlists (tags only when transitions are enabled)
        except Exception as e:
            print(f"[Transitions] playlist refresh failed: {e}", flush=True)
        return jsonify({'ok': True, 'overrides': ov.get(path) or {}})


    @app.route('/api/transitions/preview', methods=['POST'])
    def api_transitions_preview():
        """Render the transition between two songs with the real Liquidsoap transition code (takes ~10-20 s)."""
        if not _rtr:
            return jsonify({'error': 'module unavailable'}), 500
        d = request.get_json(force=True, silent=True) or {}
        a, b = d.get('a', ''), d.get('b', '')
        songs = _tr_song_set()
        if a not in songs or b not in songs or a == b:
            return jsonify({'error': 'choose two different songs from the library'}), 400
        os.makedirs(_TR_PREVIEW_DIR, exist_ok=True)
        try:                                           # drop old previews
            for fn in os.listdir(_TR_PREVIEW_DIR):
                fp = os.path.join(_TR_PREVIEW_DIR, fn)
                if time.time() - os.path.getmtime(fp) > 3600:
                    os.remove(fp)
        except Exception:
            pass
        if not _tr_preview_lock.acquire(blocking=False):
            return jsonify({'error': 'another preview is rendering - try again in a few seconds'}), 429
        try:
            pid = secrets.token_hex(6)
            out = os.path.join(_TR_PREVIEW_DIR, f'{pid}.mp3')
            r = subprocess.run(['nice', '-n', '10', _sys.executable, os.path.join(RADIO_DIR, 'rocky_transition_preview.py'), a, b, out],
                               capture_output=True, text=True, timeout=150, cwd=RADIO_DIR)
            if r.returncode != 0 or not os.path.exists(out):
                return jsonify({'error': 'preview failed', 'detail': (r.stderr or r.stdout)[-400:]}), 500
            res = json.loads(r.stdout[r.stdout.index('{'):])
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'preview timed out'}), 504
        except Exception as e:
            return jsonify({'error': f'preview failed: {e}'}), 500
        finally:
            _tr_preview_lock.release()
        res['audio'] = f'/api/transitions/preview/{pid}.mp3'
        res['a_name'], res['b_name'] = _tr_song_name(a), _tr_song_name(b)
        res.pop('out', None)
        return jsonify(res)


    @app.route('/api/transitions/preview/<pid>.mp3')
    def api_transitions_preview_audio(pid):
        if not re.fullmatch(r'[0-9a-f]{12}', pid):
            return 'not found', 404
        fp = os.path.join(_TR_PREVIEW_DIR, f'{pid}.mp3')
        if not os.path.exists(fp):
            return 'not found', 404
        return send_file(fp, mimetype='audio/mpeg', conditional=True)



