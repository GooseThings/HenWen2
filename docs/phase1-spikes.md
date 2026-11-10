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

## 5. `outstreamcmd` content and cost — **OPEN (needs the lab rig)**

Mechanism is read and documented (`app-rpt-audio-notes.md`): the configured
command gets the node's transmit-side audio on stdin as raw 8kHz slin through
a non-blocking pipe. What still needs measuring on a running node:

- that the stream really contains TXCONF-only telemetry (courtesy tone,
  `LOCALPLAY`) — the whole reason recordings stay on this path rather than a
  link;
- its cost, and whether a tiny always-on reader plus on-demand encode is the
  right shape (it runs whenever the node runs, not only when someone is
  listening);
- behavior of the 60s write-failure kill when the reader is slow.

## The lab rig — next piece of work

Spikes 1 and 5 both need a node that can be keyed without putting RF on the
air, and neither should touch 643930. This box hosts exactly two local nodes:
**643930** (`rxchannel = SimpleUSB/643930`, the real radio) and **1999**
(`rxchannel = USRP/127.0.0.1:34001:32001`, the DVSwitch bridge) — and 1999 is
*not* a safe stand-in, because keying it pushes audio into Analog_Bridge and
out to a BrandMeister talkgroup whenever the bridge is up. Networked is still
on air.

So the rig is a **throwaway second Asterisk**, the same approach used in 1.x to
reproduce the stranded-ChanSpy-leg bug: its own config/spool/log dirs, its own
AMI port (15038), only the modules needed, log size capped with `ulimit -f`,
and a node whose `rxchannel` emits continuously with no transmitter attached.
Driven by `sipp`/`pjsua` as a headless SIP client, it turns spike 1 into an
assertion — tone in on one leg, assert absent from that leg's downlink, assert
present on a second leg — with no RF keyed and no browser.

That rig is also the Phase 3+ regression harness, so it is worth building
properly rather than as a one-off.
