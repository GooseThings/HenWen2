# How app_rpt actually moves audio

Source-verified 2026-11-10 against the **3.10.8 tag** of
`AllStarLink/app_rpt` (matching the installed
`asl3-asterisk 2:22.10.1+asl3-3.10.8-1.deb13`) and Asterisk 22's
`bridges/bridge_softmix.c`. Line numbers are from those trees.

Re-fetch with:

```bash
curl -sSL https://codeload.github.com/AllStarLink/app_rpt/tar.gz/refs/tags/3.10.8 | tar xz
curl -sSLO https://raw.githubusercontent.com/asterisk/asterisk/22/bridges/bridge_softmix.c
```

## CONF and TXCONF are softmix bridges

`rpt_bridging.c:__rpt_conf_create()` builds both with
`AST_BRIDGE_CAPABILITY_MULTIMIX`. Confirmed live:

```
$ asterisk -rx "bridge show all"
77a8ce72-...  TXCONF   3  base  softmix  66:45:08
99edd38a-...  CONF     2  base  softmix  66:45:08
```

Two properties of softmix matter, both read in Asterisk 22's source:

1. **Mix-minus-self.** `softmix_process_write_audio()` subtracts each
   channel's own contribution from its own mix —
   `bridge_softmix.c:213`, `ast_slinear_saturated_subtract(&sc->final_buf[i],
   &sc->our_buf[i])`, under a comment saying exactly that.
2. **Continuous frames.** The mixing loop runs on a timer at
   `internal_mixing_interval` (20ms default) and queues a frame to *every*
   member each tick, silence included.

Property 2 is why a conference leg satisfies the "must emit audio
continuously" requirement that HenWen 1's `TAP_CHANNEL_TECHS` allowlist
exists to enforce (only SimpleUSB qualified; a quiet USRP, `Local/pseudo` or
peer channel stranded a ChanSpy leg that no Hangup variant could remove).

## A link never hears itself

A link's received audio is written to **its own** conference leg,
`l->pchan` (`app_rpt.c:5069`/`5077`), and that leg is what joins `CONF`
(`app_rpt.c:7690`). So `ast_read(l->pchan)` returns CONF minus that link —
no self-echo on any link, phone links included. Local RF receive audio is
written to `rxpchannel`, itself a CONF member, so links hear the local
receiver.

The transmitter is fed *from* the conference, and only from it:
`rxpchannel_read()` (`app_rpt.c:5373`, when `duplex != 2 && != 4`, which
includes **duplex=3**) and `pchannel_read()` (`app_rpt.c:4571`, duplex 2/4)
are the only writers into TXCONF.

Phone PTT: `*99` is `cop,6` → `DC_DOKEY` → `mylink->lastrealrx = 1`
(`rpt_functions.c:1379`, `app_rpt.c:1745`).

## `Rpt(<node>,Pm)` is a listen-only link

The `m`/`M` option sets `phone_monitor` (`app_rpt.c:7367`), and
`handle_link_phone_dtmf()` returns immediately for a monitor link
(`app_rpt.c:2199`) — so no DTMF command, `*99` included, is honored. Its
audio is also muted into the conference while `!l->lastrx`.

Only three sites set `lastrealrx = 1`: the DOKEY path above,
`rxkey_helper()` on an `AST_CONTROL_RADIO_KEY` frame (`app_rpt.c:4828` — not
something a PJSIP channel emits), and dumb-duplex-without-vox at link setup
(`app_rpt.c:7669`).

**Never use `D`/`Dv` for a listener: it pins the link keyed.**

HenWen 2.0 deliberately does *not* use `Pm` — every user connects as a
native phone-style call — but it stays documented here as the enforced
fallback for D1.

## Telemetry parity — the constraint that decides the architecture

`rpt_telemetry.c:1604-1614` joins the telemetry channel to:

- **`RPT_CONF`** for `ID1`, `PLAYBACK`, `TEST_TONE`, `STATS_GPS_LEGACY` —
  every link hears these directly.
- **`RPT_TXCONF`** for everything else: courtesy tones, and `LOCALPLAY`,
  which is how HenWen's announcer, Node ID and NWS alerts reach the air via
  `rpt localplay`.

TXCONF-only telemetry reaches links solely through the altaudio "whisper"
(`monchannel_read()`), gated on `(!l->lastrx) && (!myrpt->remrx) &&
(!myrpt->keyed)` — i.e. only while nobody is talking.

**Consequence:** a link-based feed misses any telemetry that fires mid-traffic,
such as the 10-minute forced ID during a long QSO (FCC 97.119(a)). Fine for a
human listening; not acceptable for a recording or a Broadcastify feed, where
"what went out on air" is the product. Hence: links for people,
`outstreamcmd` for machines.

## `outstreamcmd` — app_rpt's own audio export

`startoutstream()` (`app_rpt.c:1172`) spawns the configured command with the
node's **transmit-side** audio (monchannel, a TXCONF member) on its
**stdin**, as raw 8kHz slin — the exact format `audio_relay.py` already
consumes — through a non-blocking pipe. `outstream_write()` reports a write
failure once and kills the process if it persists 60s.

Gated by `((myrpt->p.duplex >= 2) || (!myrpt->keyed))`, so duplex=3 streams
always; duplex 0/1 nodes stream only while unkeyed.

It is static `rpt.conf` configuration, so changes need
`asterisk -rx "module reload app_rpt.so"` — **not** `rpt restart`, which only
softhangs each node's rxchannel and re-reads nothing.

## Local node facts (643930, as of 2026-11-10)

- `duplex = 3`, `linktolink = no`, `telemdefault = 0`.
- `phonelinkdefault` unset → `DEFAULT_PHONE_LINK_MODE` = `LINKMODE_ON` →
  `mylink->linkmode = 0x7fffffff`, so `altlink()` is true for a phone link
  and the whisper path is live.
- ASL3's stock dialplan already provides `_2.` →
  `Rpt(${EXTEN:1},P,${CALLERID(name)}-P)`; HenWen 1's own snippet uses
  `Rpt(<node>,P,0HenWen-Web)`.
