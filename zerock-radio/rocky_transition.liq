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
        o = t24 * 0.6
        o = if o > 3. then 3. else o end
        o = if oe == "decay" and o > att - 0.6 then att - 0.6 else o end
        (if o > 0. then o else 0. end, "opening uncertain (#{ic}): enters only over the quietest tail of the old song")
      else
        (0., "low confidence (#{conf}) - protect both songs, no overlap")
      end
    elsif oe == "abrupt" then
      if ie == "fade_in" and build >= 2. then
        (0.5, "abrupt ending, gentle fade-in opening - 0.5 s overlap")
      else
        (0., "abrupt ending - tight cut, no overlap")
      end
    elsif oe == "fade" or oe == "decay" then
      # the new song becomes audible when the old one is down to -18 dB (-24 dB when the opening is a full-level hit
      # or may carry vocals), earlier by the time the new opening needs to build up
      lvl = if gradual then tr_num(am, "tr_oe_t18", 0.) else tr_num(am, "tr_oe_t24", 0.) end
      o0 = lvl + (if gradual then (if build < 4. then build else 4. end) else 0. end)
      o1 = if o0 > 8. then 8. else o0 end
      att = tr_num(am, "tr_oe_att", 99.)
      if oe == "decay" and o1 > att - 0.6 then
        (if att - 0.6 > 0. then att - 0.6 else 0. end, "ring-out ending: keep the last note's attack, enter under its decay")
      else
        (o1, "#{oe} ending, #{ie} opening: new song enters when the old one is ~#{if gradual then 18 else 24 end} dB down")
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

def rocky_smart_xfade(a, b) =
  am = a.metadata
  bm = b.metadata
  if tr_str(am, "tr_v") != "1" or tr_str(bm, "tr_v") != "1" then
    # jingle or a song that is not analysed yet: legacy fixed crossfade
    log.important(label="transition", "legacy crossfade (no analysis for one of the songs)")
    rocky_xfade_legacy_w(tr_num(am, "liq_cross_duration", 5.), a, b)
  else
    window = tr_num(am, "liq_cross_duration", 5.)
    d = tr_decide(am, bm, window)
    log.important(label="transition", "#{d.oe} -> #{d.ie}: overlap #{d.overlap}s, b delay #{d.delay}s (window #{d.window}s, a_sil #{d.a_sil}, b_sil #{d.b_sil}), confidence #{d.conf} - #{d.why}")
    # optional manual gain curves (otherwise both songs keep their own audio untouched)
    oc = tr_str(am, "tr_out_curve")
    ic = tr_str(bm, "tr_in_curve")
    a_g = if oc != "" and oc != "none" then tr_ramp_out(start=window - tr_num(am, "tr_out_fade", 3.), dur=tr_num(am, "tr_out_fade", 3.), kind=tr_curve_type(oc), a.source) else a.source end
    b_g = if ic != "" and ic != "none" then fade.in(duration=tr_num(bm, "tr_in_fade", 3.), type=tr_curve_type(ic), b.source) else b.source end
    b_d = if d.delay < 0.01 then b_g else sequence(merge=true, [blank(duration=d.delay), b_g]) end
    add(normalize=false, [a_g, b_d])
  end
end
