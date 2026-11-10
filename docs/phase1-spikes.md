# Phase 1 — spike results

Five questions had to be answered before any 2.0 audio code gets written.
Source reads are done; the two that need a running node are blocked on the lab
rig, which is the next piece of work.

Source trees used (re-fetch commands in `docs/app-rpt-audio-notes.md`):
app_rpt **3.10.8** (matching the installed `2:22.10.1+asl3-3.10.8-1.deb13`)
and Asterisk 22 `bridges/bridge_softmix.c`.

## 1. Is the call downlink self-free, live? — **OPEN (gates everything)**

Source says yes, and the reasoning is in `app-rpt-audio-notes.md`: a link's
audio goes to its own conference leg, softmix subtracts a member's own
contribution, and the one path that could feed it back (`mix_altaudio()`) is
gated on `!l->lastrx`, which `*99` sets. Nothing has been *heard* yet, and
HenWen 1's `status.html:7784` comment asserts the opposite ("while keyed, is
your own voice ~a beat behind"). That comment's provenance was checked —
commit `c4c8fcf`, whose own message gives the real reason as "Listen stays the
one monitor path", and no record in `tx-spike/README.md` or the project
memory of anyone actually hearing self-audio on that downlink — so it reads as
a design-time assumption. **It still has to be falsified before Phase 2.**

Two ways to close it, either is enough:

- **Operator test, zero setup.** With TX armed and Listen off, in the browser
  console:
  ```js
  var rs = new MediaStream(_tx.session.connection.getReceivers()
      .filter(r=>r.track).map(r=>{r.track.enabled=true; return r.track}));
  var m = new Audio(); m.srcObject = rs; m.play();
  ```
  Key ~5s and talk. Expected: no self-audio while keyed, tail/courtesy tone on
  release. (One transmission; the operator's own node.)
- **Lab rig**, below — the automatable version, and the one that becomes a
  regression test.

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
