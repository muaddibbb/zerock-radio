#!/usr/bin/liquidsoap
# ZeRock Radio - Rocky stream (Liquidsoap 2.2.x)
# Rocky pattern: E E H E H J (2 English, 1 Hebrew, 1 English, 1 Hebrew, 1 Jingle)
# Shows interrupt at track boundary: Jingle -> Show -> Jingle -> Rocky

# Telnet server (web app communicates here)
settings.server.telnet := true
settings.server.telnet.port := 1234
settings.server.telnet.bind_addr := "127.0.0.1"

# Logging
settings.log.file := true
settings.log.file.path := "/home/roy/zerock-radio/logs/liquidsoap.log"
settings.log.level := 3

# Playlist files (pre-built from NAS, stored locally to avoid startup NAS scan hangs)
english_pl = "/home/roy/zerock-radio/playlists/english.m3u"
hebrew_pl  = "/home/roy/zerock-radio/playlists/hebrew.m3u"
jingles_pl = "/home/roy/zerock-radio/playlists/jingles.m3u"
zikaron_pl = "/home/roy/zerock-radio/playlists/zikaron.m3u"

# Rocky sources — each playlist filtered independently so duration skips never
# disturb the rotate's position, guaranteeing the E E H E H J order at all times.
# randomize mode shuffles the full list before repeating — far fewer repeats than random.
def make_music_src(id, pl) =
  src = playlist(id=id, mode="randomize", reload=3600, reload_mode="poll", pl)
  # Duration filter inside the source: skip tracks < 90s or > 480s.
  # Skipping within the source leaves rotate's cycle position untouched.
  filtered = source.on_track(src, fun(m) -> begin
    let dur = float_of_string(default=0., list.assoc(default="0", "duration", m))
    if dur > 0. and (dur < 90. or dur > 480.) then
      source.skip(src)
    end
  end)
  mksafe(filtered)
end

english1 = make_music_src("src_e1", english_pl)
english2 = make_music_src("src_e2", english_pl)
english3 = make_music_src("src_e3", english_pl)
hebrew1  = make_music_src("src_h1", hebrew_pl)
hebrew2  = make_music_src("src_h2", hebrew_pl)
# Jingles are intentionally short — no duration filter applied
jingle   = mksafe(playlist(id="src_j", mode="randomize", reload=3600, reload_mode="poll", jingles_pl))

# rotate() cycles through sources in strict order: E E H E H J
rocky_rotate = rotate([english1, english2, hebrew1, english3, hebrew2, jingle])

# 5-second crossfade between Rocky background tracks
def xfade(a, b) =
  add(normalize=false, [fade.out(duration=5., a.source), fade.in(duration=5., b.source)])
end
rocky = cross(duration=5., xfade, rocky_rotate)

# Show queue - web app pushes: jingle_path -> show_path -> jingle_path
# Also used for manual immediate playback
shows = request.queue(id="shows")

# Zikaron (Memorial/Holocaust Day) source — activated via interactive.bool from web UI
# When active, replaces rocky as the background. Shows still take priority.
# Every 3 zikaron songs, plays quiet.wav jingle between tracks.
quiet_jingle   = "/mnt/nas/Music/Music Reorganized/jingles/quiet.wav"
zikaron_raw    = playlist(id="src_zikaron", mode="random", reload=3600, reload_mode="poll", zikaron_pl)
zikaron_jingles = request.queue(id="zikaron_jingles")
zikaron_count  = ref(0)

# on_track fires at the START of each new zikaron song.
# After every 3rd song starts, push quiet.wav; it plays once that song finishes.
def on_zikaron_track(_) =
  zikaron_count.set(zikaron_count() + 1)
  if zikaron_count() >= 3 then
    zikaron_count.set(0)
    ignore(server.execute("zikaron_jingles.push #{quiet_jingle}"))
  end
end

zikaron_tracked = source.on_track(zikaron_raw, on_zikaron_track)

# Inner fallback: jingle queue has priority (track_sensitive=true so jingle waits for song end)
zikaron_src    = mksafe(fallback(track_sensitive=true, [zikaron_jingles, zikaron_tracked]))

zikaron_active = interactive.bool("zikaron_active", false)
zikaron_switch = switch(track_sensitive=false, [(zikaron_active, zikaron_src)])

# Background: zikaron when active (switch non-fallible), else rocky
background = fallback(track_sensitive=false, [zikaron_switch, rocky])

# Main stream: shows take priority over background
main = fallback(track_sensitive=false, [shows, background])

# Write current track info to now_playing.txt on every track start.
# This fires synchronously in Liquidsoap — gives Python instant path detection
# without depending on the 3-second queue-cache poll (critical for short jingles).
def _write_now_playing(m) =
  # "filename" is a request-level key not always forwarded through cross/fallback.
  # Fall back to "uri" then "initial_uri" so we always capture the file path.
  fname0 = list.assoc(default="", "filename", m)
  fname1 = if fname0 != "" then fname0 else list.assoc(default="", "uri", m) end
  fname  = if fname1 != "" then fname1 else list.assoc(default="", "initial_uri", m) end
  title  = list.assoc(default="", "title", m)
  artist = list.assoc(default="", "artist", m)
  if fname != "" then
    ignore(file.write(data="#{title}\t#{artist}\t#{fname}",
                      "/home/roy/zerock-radio/now_playing.txt"))
  end
end
main = source.on_metadata(main, _write_now_playing)

main = mksafe(main)

# Skip current track after 4 seconds of silence
main = blank.skip(main, max_blank=4.)

# Local Icecast output — toggle via web UI using interactive.bool
# local_active=true (default) = broadcast audio; false = silence
local_active = interactive.bool("local_active", true)
local_source  = switch(track_sensitive=false, [(local_active, main)])
local_source  = mksafe(local_source)

output.icecast(%mp3(bitrate=128),
  id          = "rocky_out",
  host        = "localhost",
  port        = 8000,
  password    = "zerock2026",
  mount       = "/stream",
  name        = "ZeRock Radio",
  description = "ZeRock Radio - Rocky 24/7",
  local_source)

# External Icecast stream — toggle via web UI using interactive.bool
# When ext_active=false the source becomes unavailable AND (because the
# icecast output is fallible) the output disconnects from icecast.live,
# releasing the /zerock mount so a different broadcaster can take over.
# When ext_active=true the output reconnects automatically.
ext_active = interactive.bool("ext_active", true)
ext_source  = switch(track_sensitive=false, [(ext_active, main)])

output.icecast(%mp3(bitrate=128),
  id          = "external_out",
  host        = "icecast.live",
  port        = 8001,
  password    = "yaniv1983",
  mount       = "/zerock",
  name        = "ZeRock Radio",
  description = "ZeRock Radio - Rocky 24/7",
  fallible    = true,
  ext_source)
