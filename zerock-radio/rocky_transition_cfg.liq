# Tunables of Rocky's crossfade (written by the /transitions page; safe to edit by hand, takes effect on the next
# Liquidsoap start - the page also applies changes live without a restart).
#   mode:         "level" = SAM-style real-time level-triggered crossfade (default); "rules" = analysis-rules engine
#   trigger_db:   the next song starts when the outgoing song's level has fallen this many dB below its body level
#   max_overlap:  the next song never starts earlier than this many seconds before the outgoing song's musical end
#   abrupt_fade:  songs that do not fade out by themselves are crossfaded over this many seconds (0 = cut)
tr_cfg_mode = "level"
tr_cfg_trigger_db = 15.
tr_cfg_max_overlap = 12.
tr_cfg_abrupt_fade = 1.5
