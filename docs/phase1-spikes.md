# Phase 1 — spike results

Five questions had to be answered before any 2.0 audio code gets written.
Source reads are done; the two that need a running node are blocked on the lab
rig, which is the next piece of work.

Source trees used (re-fetch commands in `docs/app-rpt-audio-notes.md`):
app_rpt **3.10.8** (matching the installed `2:22.10.1+asl3-3.10.8-1.deb13`)
and Asterisk 22 `bridges/bridge_softmix.c`.

## 1. Is the call downlink self-free, live? — **YES. ANSWERED 2026-11-10**

**Measured on node 643930, with no RF: it is a radioless node** (UCI-120 with
only a hand mic, no transceiver), so `*99` asserts PTT on nothing. Two
phone-portal legs on the same node, `0HenWen-SpikeA` (keys with `*99`, plays a
1 kHz tone for 3 s, unkeys with `#`) and `0HenWen-SpikeB` (listens only), each
recording its own downlink via `MixMonitor`'s `r()` split. See
`henwen-spike-selfecho` in `/etc/asterisk/custom/extensions.conf`.

| capture | 1 kHz tone | peak RMS |
|---|---|---|
| A's **write** side (what A sent into the conference) | present 3.7→6.6s, fraction **1.0** | 0.0884 |
| A's **own downlink** during that same window | **absent, fraction 0.0** | **0.0000** |
| B's downlink (control leg, same node) | present, fraction **1.0** | 0.0884 |

**Zero, not attenuated.** During its own 3-second transmission the keyed leg's
downlink is digital silence — every 100 ms window reads RMS 0.0000 — while the
control leg received the identical audio at full level. So the conference
really did carry the audio, and really did withhold it from its sender.
`bridge_softmix.c:213`'s mix-minus-self is doing exactly what the source read
said, and **HenWen 1's `status.html:7784` comment ("while keyed, is your own
voice ~a beat behind") is wrong** — that was a design-time assumption, never a
measurement.

One more thing the same recording settles: A's downlink is **live**, not
muted. It carries nothing during A's own transmission, then real audio from
~1 s after the `#` unkey (RMS 0.010–0.016 for 1.4 s) — the hang-time/courtesy
tail arriving through the altaudio whisper path, which `monchannel_read()`
gates on `!l->lastrx`, i.e. precisely when keying stops. So "hear your own tail
as confirmation", which HenWen 1 needed the post-unkey mute hold to preserve,
comes free on this path.

**This was the finding that gated the whole HenWen 2.0 direction. It holds.**

## 2. Can `rpt_auth` enforce per-user TX? — **NO, but it points somewhere better**

`rpt_auth` is a **per-node TOTP login session** that makes an extra function
stanza active (`app_rpt.c:1640-1700`): on each DTMF dispatch it walks the
authed stanza first, falling back to the source stanza. Two properties decide
it:

- It is **additive** — it grants extra commands. It cannot take `99 = cop,6`
  away from the base phone stanza.
- The session lives on `myrpt`, i.e. **per node, not per link**, so it cannot
  tell two simultaneous phone callers apart.

So D1-c as posed is a no. But it can be **inverted** into a new option:

**D1-d — remove `99 = cop,6` from the base phone functions stanza and put it
in a TOTP-authed stanza.** Then nobody can key until they log in to app_rpt
(4-digit id + 6-digit TOTP), HenWen holds each TX-permitted user's secret and
logs in on their behalf (`pyotp` is already a dependency for HenWen's own
2FA), and a `listen_only` user is simply never logged in, so their `*99` does
nothing. Everyone still connects as an identical native phone call — no `Pm`,
no second extension.

**Honest limit:** the unlocked state is the *node's*, not the user's. While an
authorized user's session is live (`RPT_AUTH_DEFAULT_TIMEOUT` 300s), a
concurrent listen-only caller's `*99` would also work. So D1-d is "TX is
unlocked for the node while an authorized operator is logged in" — stronger
than software-only, weaker than isolation. D1-b (a second extension on a node
whose functions stanza omits `cop,6`) remains the only true per-call
enforcement.

**Recommendation: D1-d, with a short auth timeout and login-per-key.** Owner's
call; D1-a as approved still stands if the residual risk is acceptable.

## 3. Does a muted mic keep sending RTP? — **ANSWERED BY DESIGN**

`tx-spike/pjsip.snippet` sets `rtp_timeout=60` because a browser that vanishes
otherwise leaves `Rpt(<node>,P)` up forever (1.x saw a 27-hour-old phone-mode
channel). So mic mute must not stop the media stream:

- **Forbidden:** `track.enabled = false`. Whether a disabled track keeps
  sending is a browser/DTX implementation detail, which is exactly the wrong
  thing to bet a hangup on.
- **Required:** gain-to-zero through the existing WebAudio chain (HenWen 1
  already has that chain for the mic-gain slider), which keeps real RTP
  flowing with silent samples. RX mute stays playback-side only.

Confirm with a real browser in Phase 3 (`rtcp` `packetsSent` while muted);
the design constraint does not depend on that measurement.

## 4. Is PJSIP realtime config available? — **YES, with one module to enable**

- `res_sorcery_realtime.so` — present **and already loaded** (`modules.conf`
  lines 208, 270).
- `res_config_sqlite3.so` — ships in the ASL3 build
  (`/usr/lib/x86_64-linux-gnu/asterisk/modules/`) but is **not loaded**:
  `modules.conf` has `autoload = no` and no `load =` line for it.

So "a user row *is* a SIP account, with no `pjsip reload`" is achievable:
add `load = res_config_sqlite3.so`, map the PJSIP object types in
`extconfig.conf`, and point them at HenWen2's own database. That is a live-
Asterisk config change, so it gets proven on the lab rig first (Phase 2).
Fallback remains a generated include file plus `pjsip reload`.

## 5. `outstreamcmd` content and cost — **ANSWERED 2026-11-10**

Measured on node 643930 (radioless, so no RF). Config added to its stanza,
`module reload app_rpt.so`, nothing else:

```
outstreamcmd = /usr/bin/tee,/tmp/henwen_outstream.raw
```

**A `module reload` is enough** — no Asterisk restart. The master thread
(`app_rpt.c:6730-6753`) starts the outstream child for any non-deleted,
non-remote node that has `outstreamcmd` set and no live pid, so the reload's
re-parse is picked up on its next pass.

**`execv()` with no shell**, args comma-delimited by `finddelim`, so
`strs[0]` must be an absolute path to an executable — no redirection, no
pipelines. stdout/stderr go to `/dev/null`; the audio is on stdin.

### Content: it carries the real on-air mix, telemetry included

Two sources injected and detected separately (Goertzel, per 100 ms window):

| injected | via | result in the capture |
|---|---|---|
| 1 kHz, 3 s | a keyed phone leg (conference audio) | **30 windows @ 1 kHz**, rms 0.0884 |
| 1600 Hz, 3 s | `rpt localplay` (**TXCONF-only** telemetry) | **30 windows @ 1600 Hz**, rms 0.0626 |

No cross-contamination: 1 kHz absent from the localplay window, 1600 Hz
absent from the conference window.

**The second row is the one that decides the architecture.** `LOCALPLAY`
joins `RPT_CONF`'s sibling `RPT_TXCONF` only (`rpt_telemetry.c:1604-1614`),
so it reaches links solely through the idle-gated altaudio whisper — a
link-based recording can miss it. It appears in the `outstreamcmd` feed at
full level, because monchannel is a TXCONF member. So recordings and the
stream relay belong on this path, exactly as the playbook assumed. That
assumption is now measured.

### Cost: negligible, and continuous

The feed is **continuous whether or not anyone is talking** — 15.4 kB/s
exactly (8000 samples/s x 2 bytes), because monchannel is a softmix member
and the mixing loop queues a frame to every member every 20 ms. Over 83 s the
reader (`tee`) used 0.1% CPU, 0 s of CPU time and 2 MB RSS.

That continuity is the same property that makes a conference leg immune to
the stranded-ChanSpy problem `TAP_CHANNEL_TECHS` exists for, and it means the
shape is "always-on cheap reader, encode only on demand" rather than
start/stop per listener.

### Two operational gotchas

- **Removing `outstreamcmd` and reloading stops the audio but does not kill
  the child.** Confirmed: the capture stopped growing immediately, and the
  `tee` process was still alive and holding its stdin pipe until killed by
  hand. `stop_outstream()` only runs on a sustained write failure or node
  shutdown, so a config change leaves an orphan. Whatever HenWen2 puts here
  has to be safe to leave running, or be reaped deliberately.
- **A command that exits within one second disables the feature**: app_rpt
  logs `outstreamcmd '%s' appears to be broken, disabling` and clears
  `p.outstreamcmd` until the next reload.

## Postscript: no separate lab Asterisk was needed

The plan called for a throwaway Asterisk because both open spikes needed a
node that could be keyed without putting RF on the air. It turned out 643930
**is** such a node: the owner confirmed it is radioless — a UCI-120 with only
a hand mic, no transceiver, linking to other nodes and otherwise islanded.
So `*99` asserts PTT on nothing, and both spikes ran against the real
production Asterisk with real app_rpt, real softmix and the real phone portal.

The control leg for spike 1 came from putting **two** phone-portal legs on the
same node rather than linking a second node, which also avoided needing a new
node's thread started (`module reload` does not spawn one — see
`selfecho-spike-harness` notes).

A `[1998]` stanza with `rxchannel = Local/pseudo` is still in `rpt.conf` from
the first approach. It is threadless and harmless (one "hung" warning at
reload, not repeating) and is left in place for whenever an app_rpt
unload/load is acceptable.
