# HenWen 2.0 — AllStarLink 3 Node Manager & Kiosk

A browser-based manager and public kiosk display for an AllStarLink 3 node,
running as a systemd service next to Asterisk.

**What's new in 2.0:** listening and transmitting from the browser are a real
VoIP call into the node. Each account with a callsign gets its own SIP login,
shows up under Connected Nodes by callsign, hears the node without hearing
itself, and keys up with push-to-talk. Accounts without a callsign keep the
1.x stream.

> **Work in progress.** 2.0 is forked from
> [HenWen 1.x](https://github.com/GooseThings/HenWen) and is being built in
> phases; some of the 1.x audio code is still here until a phase retires it.

![Kiosk](images/Screenshot_2026-10-07_142154.jpg)

## Highlights

- **Node management:** `rpt.conf` editor, connect/disconnect/monitor, Smart Connector schedules
- **Audio:** Listen and push-to-talk over one call per user, recording, Broadcastify/YouTube relay, scheduled and TTS announcements, NWS alerts
- **Status Board:** full-screen kiosk with a live map, no login needed to view
- **Accounts:** Owner/Superuser/Admin/Kiosk roles, 2FA, per-user SIP credentials

## Requirements

AllStarLink 3 on Debian 12/13, Python 3.8+, root, `ffmpeg`. Minimum hardware
is a Raspberry Pi 4 (4GB) or a 2+ core x86-64 machine. Browser audio needs
HTTPS.

## Install

There is no 2.0 installer or upgrade path from 1.x yet (Phase 6). For now,
run [HenWen 1.x](https://github.com/GooseThings/HenWen#install).

## Documentation

- **[HenWen 2.0 wiki](https://github.com/GooseThings/HenWen2/wiki)** — what changed in 2.0, per-user SIP setup, Connected Nodes, [running with the ASL Web UI](https://github.com/GooseThings/HenWen2/wiki/Running-with-the-ASL-Web-UI)
- [`docs/HENWEN2-PLAYBOOK.md`](docs/HENWEN2-PLAYBOOK.md) — plan, phase status, decisions
- [`docs/ASL-WEB-UI-PLAN.md`](docs/ASL-WEB-UI-PLAN.md) — what HenWen hands over to AllStarLink's new web UI, and what it keeps
- [HenWen 1.x wiki](https://github.com/GooseThings/HenWen/wiki) — everything shared with 1.x (install, features, troubleshooting)

## Status and roadmap

Phases 0-3 are done: per-user SIP accounts, one call for Listen and
push-to-talk, and listeners shown by callsign. Next is keyed state for
listeners, then recording and the stream relay on the new audio path. HenWen
also runs alongside AllStarLink's ASL Web UI and hands node configuration
over to it where that UI is set up. See the
wiki's [Status](https://github.com/GooseThings/HenWen2/wiki/Status) page.

## Support and contributing

Questions and bug reports go to [issues](https://github.com/GooseThings/HenWen2/issues);
pull requests are welcome. Maintained by N8GMZ. See [CHANGELOG.md](CHANGELOG.md).

## License

GPL-3.0 — use at your own risk. Not affiliated with AllStarLink, Inc.

*73 de N8GMZ dit dit*
