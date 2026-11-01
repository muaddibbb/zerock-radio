#!/usr/bin/env python3
"""Find WordPress episodes (zerockradio.com) that have no "Show" tag, and optionally tag them.

  python3 wp_untagged_episodes.py list [out.txt]      # public API, no login; writes a tab-separated report
  python3 wp_untagged_episodes.py tag [--limit N]     # RUN ON ROCKY: adds the show term (slug 'archive',
                                                      # "ארכיון") to every untagged episode, resumable

`tag` takes the WP admin app-password from the zerock-radio-web process environment
(WP_USERNAME / WP_APP_PASSWORD); nothing is printed or stored. Episodes that got a show in the
meantime are skipped. Progress is kept in /tmp/archive_done.json so a rerun resumes.
"""
import base64, html, json, subprocess, sys, time, urllib.error, urllib.request

SITE = 'https://zerockradio.com'
API = SITE + '/wp-json/wp/v2'
TERM_SLUG = 'archive'            # the "ארכיון" term in the `shows` taxonomy (id 328 on 2026-10-17)
UA = {'User-Agent': 'Mozilla/5.0'}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r), r.headers


def all_episodes():
    rows, page = [], 1
    while True:
        try:
            d, h = _get(f"{API}/episodes?per_page=100&page={page}&_fields=id,date,slug,link,status,title.rendered,shows")
        except urllib.error.HTTPError as e:
            if e.code == 400:      # past the last page
                break
            raise
        rows += d
        if not d or page >= int(h.get('X-WP-TotalPages') or 1):
            break
        page += 1
    return rows


def untagged():
    return sorted((r for r in all_episodes() if not r.get('shows')), key=lambda r: r['date'], reverse=True)


def term_id():
    d, _ = _get(f"{API}/shows?slug={TERM_SLUG}&_fields=id,name")
    if not d:
        sys.exit(f"show term with slug '{TERM_SLUG}' not found - create it in WP admin (Episodes > Shows) first")
    return d[0]['id']


def cmd_list(out='episodes_without_show.txt'):
    rows = untagged()
    lines = [f"WordPress episodes with NO Show tag ({len(rows)} published, newest first)", "",
             "ID\tDate\tTitle\tLink\tEdit"]
    for r in rows:
        lines.append("\t".join([str(r['id']), r['date'][:10], html.unescape(r['title']['rendered']).replace("\t", " "),
                                r['link'], f"{SITE}/wp-admin/post.php?post={r['id']}&action=edit"]))
    open(out, 'w', encoding='utf-8').write("\n".join(lines) + "\n")
    print(f"{len(rows)} episodes without a show -> {out}")


def cmd_tag(limit=None):
    pid = subprocess.check_output(['systemctl', 'show', 'zerock-radio-web', '-p', 'MainPID', '--value']).decode().strip()
    env = dict(l.split('=', 1) for l in open(f'/proc/{pid}/environ', 'rb').read().decode().split('\0') if '=' in l)
    auth = {'Authorization': 'Basic ' + base64.b64encode(f"{env['WP_USERNAME']}:{env['WP_APP_PASSWORD']}".encode()).decode(),
            'Content-Type': 'application/json'}
    term = term_id()
    ids = [r['id'] for r in untagged()]
    done_f = '/tmp/archive_done.json'
    try:
        done = set(json.load(open(done_f)))
    except Exception:
        done = set()
    print(f"{len(ids)} untagged episodes, tagging with show term {term}", flush=True)
    ok = skipped = failed = 0
    for n, i in enumerate(ids):
        if limit is not None and n >= limit:
            break
        if i in done:
            continue
        try:
            req = urllib.request.Request(f"{API}/episodes/{i}?context=edit&_fields=id,shows", headers={**UA, **auth})
            cur = json.load(urllib.request.urlopen(req, timeout=60))
            if cur.get('shows'):
                skipped += 1
                done.add(i)
                continue                      # got a show meanwhile - leave it alone
            req = urllib.request.Request(f"{API}/episodes/{i}", data=json.dumps({'shows': [term]}).encode(),
                                         headers={**UA, **auth}, method='POST')
            res = json.load(urllib.request.urlopen(req, timeout=60))
            if term in (res.get('shows') or []):
                ok += 1
                done.add(i)
            else:
                failed += 1
                print('NOT APPLIED', i, flush=True)
        except urllib.error.HTTPError as e:
            failed += 1
            print('HTTP', e.code, i, flush=True)
            if e.code in (401, 403, 429):
                break                         # auth / rate limit: stop instead of hammering
        except Exception as e:
            failed += 1
            print('ERR', i, repr(e)[:120], flush=True)
        if (ok + failed) % 50 == 0:
            json.dump(sorted(done), open(done_f, 'w'))
            print(f'progress ok={ok} skipped={skipped} failed={failed}', flush=True)
        time.sleep(0.25)
    json.dump(sorted(done), open(done_f, 'w'))
    print(f'FINISHED ok={ok} skipped={skipped} failed={failed}', flush=True)


if __name__ == '__main__':
    a = sys.argv[1:] or ['list']
    if a[0] == 'list':
        cmd_list(*(a[1:2]))
    elif a[0] == 'tag':
        cmd_tag(int(a[a.index('--limit') + 1]) if '--limit' in a else None)
    else:
        sys.exit(__doc__)
