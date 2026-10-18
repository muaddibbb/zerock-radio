# Smart per-pair crossfade for Rocky music (Liquidsoap 2.2.x).
# Included by rocky.liq and by the preview renderer, so what you preview is exactly what airs.
#
# Every music line in english_liq.m3u / hebrew_liq.m3u carries tags written by rocky_transitions.py:
#   tr_oe / tr_ie        how the song ENDS / OPENS   (fade, decay, abrupt, unknown / hit, full, fade_in,
#                        quiet_intro, single_note, unknown)
#   tr_oe_conf/ie_conf   analysis confidence 0..1 (a heuristic score, not a probability)
#   tr_oe_sil / ie_sil   technical silence at the file end / start (trimmed so it never causes a gap)
#   tr_oe_t12/t18/t24    seconds before the audible end at which the song is still within 12/18/24 dB of its body level
#   tr_oe_att            seconds before the audible end of the song's last strong attack
#   tr_ie_t12            seconds until the opening is within 12 dB of its body level ("audibly meaningful")
#   tr_out_curve ...     optional manual gain curves (none|lin|sin|exp|log) + durations
#   liq_cross_duration   how much of the outgoing song cross() may use (set per song)
# From these and ONLY these, tr_decide() chooses, for the actual pair, how far before the outgoing song's
# audible end the incoming song becomes audible ("overlap"). It never changes either song's own audio
# unless a manual curve is set; a song that is not analysed, or an uncertain analysis, gets the
# conservative choice (no overlap: finish the old song, then start the new one) or the legacy 5 s crossfade.

def tr_num(m, k, d) =
  float_of_string(default=d, list.assoc(default="", k, m))
end

def tr_str(m, k) =
  list.assoc(default="", k, m)
end

# Decision for one pair. am/bm = metadata of outgoing / incoming song. window = seconds of the
# outgoing song the crossfade can use.
def tr_decide(am, bm, window) =
  oe = tr_str(am, "tr_oe")
  ie = tr_str(bm, "tr_ie")
  oc = tr_num(am, "tr_oe_conf", 0.)
  ic = tr_num(bm, "tr_ie_conf", 0.)
  conf = if oc < ic then oc else ic end
  # the music of an abruptly stopped song ended tr_oe_mend s before its "audible" end (quiet dust after it)
  a_sil = tr_num(am, "tr_oe_sil", 0.) + tr_num(am, "tr_oe_mend", 0.)
  b_sil = tr_num(bm, "tr_ie_sil", 0.)
  gradual = ie == "fade_in" or ie == "quiet_intro" or ie == "single_note"
  build = tr_num(bm, "tr_ie_t12", 0.)
  nov = tr_str(am, "tr_no_overlap") == "true" or tr_str(bm, "tr_no_overlap") == "true"

  # (overlap seconds, short explanation)
  choice =
    if nov then
      (0., "manual: no overlap")
    elsif conf < 0.55 then
      if (oe == "fade" or oe == "decay") and oc >= 0.55 then
        # the ending is reliable but the opening is not: let the new song in only over the quietest part of the
        # old song's ending (it is ~24 dB down or more there), never over its music
        t24 = tr_num(am, "tr_oe_t24", 0.)
        att = tr_num(am, "tr_oe_att", 99.)
        o = if t24 > 4. then 4. else t24 end
        o = if oe == "decay" and o > att - 0.6 then att - 0.6 else o end
        (if o > 0. then o else 0. end, "opening uncertain (#{ic}): enters only over the quietest tail of the old song")
      else
        (0., "low confidence (#{conf}) - protect both songs, no overlap")
      end
    elsif oe == "abrupt" then
      if gradual then
        # a quiet / fading / single-note opening may start under the last moment of a hard stop (it is inaudible
        # beneath the full-level end, and it removes the dead moment between the stop and the quiet start)
        o = if build * 0.5 > 1.5 then 1.5 else build * 0.5 end
        (o, "abrupt ending, soft opening: the new song's intro starts under the last moment of the old one")
      else
        (0., "abrupt ending, strong opening - tight cut, no overlap")
      end
    elsif oe == "fade" or oe == "decay" then
      # the new song becomes audible when the old one is down to -18 dB (-24 dB when the opening is a full-level hit
      # or may carry vocals), earlier by the time the new opening needs to build up
      # (a sparse ring-out has no singing in it, so a hit may come in a bit earlier over it than over a busy fade)
      early = gradual or oe == "decay"
      lvl_db = if early then 18 else 24 end
      lvl = if early then tr_num(am, "tr_oe_t18", 0.) else tr_num(am, "tr_oe_t24", 0.) end
      o0 = lvl + (if gradual then (if build < 4. then build else 4. end) else 0. end)
      o1 = if o0 > 8. then 8. else o0 end
      att = tr_num(am, "tr_oe_att", 99.)
      if oe == "decay" and o1 > att - 0.6 then
        (if att - 0.6 > 0. then att - 0.6 else 0. end, "ring-out ending: keep the last note's attack, enter under its decay")
      else
        (o1, "#{oe} ending, #{ie} opening: new song enters when the old one is ~#{lvl_db} dB down")
      end
    else
      (0., "unknown ending - no overlap")
    end
  let (ov0, why) = choice
  room = window - a_sil - 0.3
  ov = if ov0 > room then (if room > 0. then room else 0. end) else ov0 end
  # delay of the incoming file's start inside the crossfade window (its own leading silence is skipped)
  delay0 = window - a_sil - ov - b_sil
  delay = if delay0 < 0. then 0. else delay0 end
  {overlap = ov, delay = delay, why = why, conf = conf, oe = oe, ie = ie, a_sil = a_sil, b_sil = b_sil, window = window}
end

def tr_curve_type(s) =
  if s == "lin" or s == "sin" or s == "log" or s == "exp" then s else "sin" end
end

# cross() hands the OUTPUT the outgoing song's metadata at the start of every transition and never the incoming
# song's (add() keeps the first source's metadata), so the "now playing" label (now_playing.txt, Icecast title, the
# app) was always one song behind. This inserts the incoming song's metadata `at` seconds after the start of the
# transition, i.e. when that song actually becomes audible.
def tr_announce(at, m, s) =
  s = insert_metadata(s)
  n = ref(0)
  sent = ref(false)
  source.on_frame(s, fun() -> begin
    n := n() + 1
    if not sent() and float_of_int(n()) * 0.04 >= at then
      sent := true
      s.insert_metadata(m)
    end
  end)
end

# fade.out() cannot be used on the crossfade buffer when the cross window is longer than the fade (it starts fading at the
# beginning of the buffer), so the end-of-song fade is an explicit gain ramp: it starts `start` seconds after the
# beginning of the old song's buffered tail and lasts `dur` seconds (frames are 0.04 s).
def tr_ramp_out(~start, ~dur, ~kind, s) =
  t = ref(0.)
  s = source.on_frame(s, fun() -> t := t() + 0.04)
  g = fun() -> begin
    u0 = if dur <= 0. then 1. else (t() - start) / dur end
    u = if u0 < 0. then 0. elsif u0 > 1. then 1. else u0 end
    if kind == "lin" then 1. - u
    elsif kind == "exp" then (exp(-6.9 * u) - 0.001) / 0.999
    elsif kind == "log" then 1. - u * u
    else cos(u * 1.5707963) end
  end
  amplify(g, s)
end

def rocky_xfade_legacy(a, b) =
  add(normalize=false, [fade.out(duration=5., a.source), fade.in(duration=5., b.source)])
end

# The legacy 5 s crossfade, but aware of a longer cross window (set per song by the tags): the new song starts
# exactly 5 s before the old one's end, as it always did, instead of at the start of the window.
def rocky_xfade_legacy_w(window, a, b) =
  d = window - 5.
  b_g = fade.in(duration=5., b.source)
  b_d = if d < 0.01 then b_g else sequence(merge=true, [blank(duration=d), b_g]) end
  add(normalize=false, [tr_ramp_out(start=window - 5., dur=5., kind="sin", a.source), b_d])
end

# The real length of the outgoing song's buffered tail. It is NOT always the window set by the tags: cross() only
# overlaps as much as BOTH items can give, so a jingle shorter than the window shrinks it (and a skip does too).
def tr_window(a, am) =
  rem = source.remaining(a.source)
  if rem > 0. and rem < 1000. then rem else tr_num(am, "liq_cross_duration", 2.5) end
end

# Crossfade used whenever a JINGLE is involved (one side has no analysis tags): 2 seconds, positioned on the
# outgoing item's musical end (its technical silence is skipped). `ramp_a` fades the outgoing item over those
# 2 s (needed for jingles and for songs that stop abruptly; a song that already fades or rings out is left alone).
jingle_xfade_secs = 2.

def rocky_xfade_jingle(~a_end_sil, ~b_sil, ~ramp_a, window, a, b) =
  ov = jingle_xfade_secs
  e = window - a_end_sil
  d0 = e - ov - b_sil
  d = if d0 < 0. then 0. else d0 end
  a_g = if ramp_a then tr_ramp_out(start=e - ov, dur=ov, kind="sin", a.source) else a.source end
  b_g = fade.in(duration=ov, b.source)
  b_d = if d < 0.01 then b_g else sequence(merge=true, [blank(duration=d), b_g]) end
  log.important(label="transition", "jingle crossfade #{ov}s (window #{window}s, outgoing silence #{a_end_sil}s, incoming silence #{b_sil}s)")
  tr_announce(d + b_sil, b.metadata, add(normalize=false, [a_g, b_d]))
end

# Set (by rocky.liq) to time() right before it skips the background after a show ended. The background kept playing
# silently during the show and the closing jingle, so the transition that follows a skip would otherwise play the stale
# tail of a song nobody heard the start of; for those few seconds we drop the old tail and start the new song cleanly.
rocky_resume_skip_t = ref(-100.)

# ── SAM-Broadcaster-style smart crossfade: driven by the REAL-TIME level of the outgoing song ────────────────────
# Instead of deciding from a pre-computed table, the outgoing song's actual output level is metered while it plays
# (250 ms RMS). The next song starts the moment that level has fallen `trigger_db` dB below the song's body level (a
# fade-out / ring-out that has really become quiet), but never earlier than `max_overlap` s before the song's musical
# end. A song that is still loud when its end arrives (a hard stop) is crossfaded over `abrupt_fade` s instead.
# Per-song analysis tags are only used for: the body level (tr_oe_body), the trailing silence to skip (tr_oe_sil +
# tr_oe_mend) and the incoming song's leading silence (tr_ie_sil).
%include "/home/roy/zerock-radio/rocky_transition_cfg.liq"
tr_trigger_db = interactive.float("tr_trigger_db", tr_cfg_trigger_db)
tr_max_overlap = interactive.float("tr_max_overlap", tr_cfg_max_overlap)
tr_abrupt_fade = interactive.float("tr_abrupt_fade", tr_cfg_abrupt_fade)
# set by the app (time()) right before a manual Skip: the old song then fades out quickly instead of playing on
tr_skip_t = interactive.float("tr_skip_t", -100.)

def rocky_level_xfade(a, b) =
  am = a.metadata
  bm = b.metadata
  oe = tr_str(am, "tr_oe")
  ie = tr_str(bm, "tr_ie")
  body = tr_num(am, "tr_oe_body", -18.)
  a_end = tr_num(am, "tr_oe_sil", 0.) + tr_num(am, "tr_oe_mend", 0.)
  b_sil = tr_num(bm, "tr_ie_sil", 0.)
  oc = tr_num(am, "tr_oe_conf", 0.)
  ic = tr_num(bm, "tr_ie_conf", 0.)
  conf = if oc < ic then oc else ic end
  trig = tr_trigger_db()
  maxov = tr_max_overlap()
  fade_len = tr_abrupt_fade()
  skipped = time() - tr_skip_t() < 4.
  window = tr_window(a, am)

  meter = rms(duration=0.25, a.source)
  started = ref(false)
  forced = ref(false)
  t0 = ref(0.)
  fl = ref(fade_len)
  n = ref(0)
  now = fun() -> float_of_int(n()) * 0.04
  musical_rem = fun() -> source.remaining(a.source) - a_end

  def fire(is_forced, lvl, why) =
    started := true
    forced := is_forced
    t0 := now()
    if skipped then fl := 0.6 end
    rem = musical_rem()
    ov = if rem - b_sil > 0. then rem - b_sil else 0. end
    note = if is_forced then "old song faded over " ^ string(fl()) ^ " s" else "no fade needed" end
    at = now()
    log.important(label="transition", "#{oe} -> #{ie}: overlap #{ov}s, b delay #{at}s (window #{window}s, a_sil #{a_end}, b_sil #{b_sil}), confidence #{conf} - level-triggered (#{why}): old song at #{lvl} dB, body #{body} dB, #{note}")
  end

  low = ref(0)
  # an early start (before the song's musical end) is only allowed for endings that really fade / ring out
  natural = (oe == "fade" or oe == "decay") and oc >= 0.5
  a_watch = source.on_frame(meter, fun() -> begin
    n := n() + 1
    if not started() then
      lvl = dB_of_lin(meter.rms() + 0.000001)
      if n() > 12 and lvl < body - trig then low := low() + 1 else low := 0 end
      rem = musical_rem()
      if skipped then
        fire(true, lvl, "manual skip")
      elsif natural and low() >= 12 and rem <= maxov + b_sil then
        fire(false, lvl, "level fell " ^ string(trig) ^ " dB")
      elsif rem <= fade_len + b_sil then
        fire(n() > 12 and lvl >= body - trig, lvl, "end of the song reached")
      end
    end
  end)

  def shape(u0) =
    u = if u0 < 0. then 0. elsif u0 > 1. then 1. else u0 end
    sin(u * 1.5707963)
  end
  a_gain = fun() -> if forced() then shape(1. - (now() - t0()) / fl()) else 1. end
  b_gain = fun() -> if forced() then shape((now() - t0()) / fl()) else 1. end
  a_amp = amplify(a_gain, a_watch)
  b_amp = amplify(b_gain, b.source)
  b_sw = switch(track_sensitive=false, [({started()}, b_amp)])

  res = insert_metadata(add(normalize=false, [a_amp, b_sw]))
  sent = ref(false)
  source.on_frame(res, fun() -> begin
    if started() and not sent() and now() >= t0() + b_sil then
      sent := true
      res.insert_metadata(bm)
    end
  end)
end

def rocky_smart_xfade(a, b) =
  am = a.metadata
  bm0 = b.metadata
  if time() - rocky_resume_skip_t() < 3. then
    log.important(label="transition", "after a show: dropping the stale tail of the background song, new song starts cleanly")
    tr_announce(0., bm0, fade.in(duration=0.4, b.source))
  elsif tr_str(am, "tr_v") != "1" then
    # the outgoing item is a jingle (or a song not analysed yet): 2 s crossfade
    rocky_xfade_jingle(a_end_sil=0., b_sil=tr_num(bm0, "tr_ie_sil", 0.), ramp_a=true, tr_window(a, am), a, b)
  elsif tr_str(bm0, "tr_v") != "1" then
    # song -> jingle: the jingle comes in 2 s before the song's musical end
    oe0 = tr_str(am, "tr_oe")
    self_fading = (oe0 == "fade" or oe0 == "decay") and tr_num(am, "tr_oe_conf", 0.) >= 0.55
    rocky_xfade_jingle(a_end_sil=tr_num(am, "tr_oe_sil", 0.) + tr_num(am, "tr_oe_mend", 0.), b_sil=0., ramp_a=not self_fading, tr_window(a, am), a, b)
  elsif tr_cfg_mode == "level" and tr_str(am, "tr_no_overlap") != "true" and tr_str(bm0, "tr_no_overlap") != "true" and (tr_str(am, "tr_out_curve") == "" or tr_str(am, "tr_out_curve") == "none") and (tr_str(bm0, "tr_in_curve") == "" or tr_str(bm0, "tr_in_curve") == "none") then
    rocky_level_xfade(a, b)
  else
    bm = bm0
    window = tr_window(a, am)
    d = tr_decide(am, bm, window)
    log.important(label="transition", "#{d.oe} -> #{d.ie}: overlap #{d.overlap}s, b delay #{d.delay}s (window #{d.window}s, a_sil #{d.a_sil}, b_sil #{d.b_sil}), confidence #{d.conf} - #{d.why}")
    # optional manual gain curves (otherwise both songs keep their own audio untouched)
    oc = tr_str(am, "tr_out_curve")
    ic = tr_str(bm, "tr_in_curve")
    a_g = if oc != "" and oc != "none" then tr_ramp_out(start=window - tr_num(am, "tr_out_fade", 3.), dur=tr_num(am, "tr_out_fade", 3.), kind=tr_curve_type(oc), a.source) else a.source end
    b_g = if ic != "" and ic != "none" then fade.in(duration=tr_num(bm, "tr_in_fade", 3.), type=tr_curve_type(ic), b.source) else b.source end
    b_d = if d.delay < 0.01 then b_g else sequence(merge=true, [blank(duration=d.delay), b_g]) end
    tr_announce(d.delay + d.b_sil, bm0, add(normalize=false, [a_g, b_d]))
  end
end
