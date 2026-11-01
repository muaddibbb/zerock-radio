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


def register(app, RADIO_DIR, _rocky_loudness=None, lq_send=None):
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



    # ── crossfade settings (SAM-style level-triggered engine, see rocky_transition_cfg.liq) ─────────────────
    _CFG_FILE = f"{RADIO_DIR}/rocky_transition_cfg.liq"
    _CFG_KEYS = {  # name -> (liq variable, live variable, min, max)
        'trigger_db':  ('tr_cfg_trigger_db',  'tr_trigger_db',  3.0, 40.0),
        'max_overlap': ('tr_cfg_max_overlap', 'tr_max_overlap', 1.0, 20.0),
        'abrupt_fade': ('tr_cfg_abrupt_fade', 'tr_abrupt_fade', 0.0, 6.0),
    }

    def _read_cfg():
        out = {'mode': 'level', 'trigger_db': 15.0, 'max_overlap': 12.0, 'abrupt_fade': 1.5}
        try:
            txt = open(_CFG_FILE, encoding='utf-8').read()
            m = re.search(r'^tr_cfg_mode\s*=\s*"(\w+)"', txt, re.M)
            if m:
                out['mode'] = m.group(1)
            for k, (var, _live, _lo, _hi) in _CFG_KEYS.items():
                m = re.search(r'^' + var + r'\s*=\s*(-?[\d.]+)', txt, re.M)
                if m:
                    out[k] = float(m.group(1).rstrip('.') or 0)
        except FileNotFoundError:
            pass
        return out

    def _write_cfg(c):
        txt = ('# Tunables of Rocky\'s crossfade (written by the /transitions page; safe to edit by hand, takes effect on the\n'
               '# next Liquidsoap start - the page also applies changes live without a restart).\n'
               '#   mode:         "level" = SAM-style real-time level-triggered crossfade (default); "rules" = analysis-rules engine\n'
               '#   trigger_db:   the next song starts when the outgoing song\'s level has fallen this many dB below its body level\n'
               '#   max_overlap:  the next song never starts earlier than this many seconds before the outgoing song\'s musical end\n'
               '#   abrupt_fade:  songs that do not fade out by themselves are crossfaded over this many seconds (0 = cut)\n'
               f'tr_cfg_mode = "{c["mode"]}"\n'
               f'tr_cfg_trigger_db = {c["trigger_db"]:.1f}\n'
               f'tr_cfg_max_overlap = {c["max_overlap"]:.1f}\n'
               f'tr_cfg_abrupt_fade = {c["abrupt_fade"]:.2f}\n')
        tmp = _CFG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(txt)
        os.replace(tmp, _CFG_FILE)

    @app.route('/api/transitions/settings', methods=['GET', 'POST'])
    def api_transitions_settings():
        if request.method == 'GET':
            return jsonify(_read_cfg())
        d = request.get_json(force=True, silent=True) or {}
        c = _read_cfg()
        if d.get('mode') in ('level', 'rules'):
            c['mode'] = d['mode']
        for k, (_var, _live, lo, hi) in _CFG_KEYS.items():
            if k in d and d[k] not in (None, ''):
                try:
                    v = float(d[k])
                except (TypeError, ValueError):
                    return jsonify({'error': f'bad {k}'}), 400
                if not (lo <= v <= hi):
                    return jsonify({'error': f'{k} must be between {lo} and {hi}'}), 400
                c[k] = v
        _write_cfg(c)
        live = []
        if lq_send:
            try:
                resp = lq_send([f'var.set {live_name} = {c[k]:.2f}' for k, (_v, live_name, _l, _h) in _CFG_KEYS.items()])
                live = (resp or '').strip()[:200]
            except Exception as e:
                live = f'live update failed: {e}'
        return jsonify({'ok': True, 'settings': c, 'live': live, 'note': 'mode changes apply at the next Rocky restart'})

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



