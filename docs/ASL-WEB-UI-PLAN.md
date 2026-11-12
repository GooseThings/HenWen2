# HenWen and the ASL Web UI

AllStarLink released a web UI for ASL3 (`asl-web-ui`, 1.0-1 on 2026-11-11;
docs at https://allstarlink.github.io/web-ui/). Part of it does what HenWen
Manager does. This plan says what HenWen hands over to it, what HenWen keeps,
and what has to be fixed first so the two can run on one box.

The phases are named **W0-W4** so they don't collide with the 2.0 playbook's
Phases 0-6 (`docs/HENWEN2-PLAYBOOK.md`).

## What the ASL Web UI is

Read from the 1.0-1 package itself (`apt-get download asl-web-ui`, unpacked,
**not installed**), not only from the docs:

- An aiohttp service running as `asterisk` behind a Unix socket, plus a root
  helper (`asl-web-ui-priv`) for privileged actions. Depends on
  `asl3-asterisk`, `asl3-menu`, `apache2`, `python3-certbot-apache`, Debian 13
  (`python3 >= 3.13`).
- Login is PAM: any Linux account in the `asl-web-ui` group, except root.
  HTTPS only. **No roles** — everyone who can log in can do everything,
  including the Asterisk CLI, config editing and reboot.
- Config edits are small and lossless (`configengine/edit.py`), shown as a
  diff before Apply, snapshotted (last 50) and audited
  (`/var/lib/asl-web-ui/audit.jsonl`), with a "restart needed" banner.
- Pages: Overview, Nodes (General, Radio interface, Tuning, Voter, Access
  control, Customizations; add/remove/**rename**), Asterisk (start/stop,
  registrations, **AMI secret**, **IAX port**, console), Allmon3, SA818,
  Diagnostics (node auth check, network checks, packages, update *check*,
  Asterisk log), Backups (`asl-backup-menu` archives and cloud), HTTPS,
  Network/44Net, Manual Config Edit (`/etc/asterisk`, `/etc/allmon3`).
- It does **not** connect or disconnect links, show live link activity,
  carry audio, schedule anything, play IDs or announcements, or give people
  accounts that aren't Linux accounts.

`asl3-appliance` 3.0.0 bundles it. The production box has 2.2.2, so a routine
`apt upgrade` will install it. W0 is therefore not optional.

## Coexistence hazards (W0)

Each was checked against the package source.

1. **AMI user `admin`.** HenWen logs in as `admin` (`AMI_USER=admin` in the
   unit; `ami-setup.sh` defaults to it). The UI's Asterisk > Settings > AMI
   Secret rewrites exactly `[admin]` in `manager.conf` and updates only
   Allmon3 (`nodes/planner.py: set_ami_secret`). One click and HenWen loses
   AMI: board, connects, keyed monitors. **Fix:** HenWen gets its own AMI user.
2. **Global Apache alias.** The package runs `a2enconf asl-web-ui`, which
   serves `/asl-web-ui/` on *every* virtual host — on a shared web server
   that includes unrelated sites. On HenWen's own vhost, `ProxyPass /` to
   Flask competes with the alias. **Fix:** a script that scopes the UI to
   HenWen's vhost(s) and excludes `/asl-web-ui` from HenWen's catch-all proxy.
3. **Two writers to `rpt.conf`.** HenWen writes it from General Settings
   (`/api/save`), the raw editor, backup restore and DVSwitch apply. Neither
   tool's history sees the other's writes, and a save from a stale page
   silently overwrites a newer edit. **Fix:** HenWen refuses a save when the
   file changed since the page loaded it.
4. **Node rename, remove, IAX port.** The UI renames a node across
   `rpt.conf`, AstDB allow/deny lists and Allmon3 (`planner.rename`), and can
   remove one. HenWen keys connectors, ID configs, lockouts, favorites,
   permanent/temp links, DVSwitch and the SIP dialplan by node number; none of
   that follows. **Fix:** detect node numbers HenWen references that Asterisk
   no longer runs, and say so in Manager.
5. **Let's Encrypt.** The UI runs `certbot certonly --apache` (it never edits
   other sites) and, in its default `own` mode, writes its own
   `sites-available/asl-web-ui-https.conf`. It enables `default-ssl` only when
   nothing serves port 443. The real risk is an operator requesting a
   certificate for HenWen's hostname from the UI: two port-443 vhosts with the
   same `ServerName`, first one wins. **Fix:** documentation — let
   `setup-https.sh` own HenWen's hostname; don't request it from the UI.
6. **`/etc/asterisk/custom/`.** The UI's Customizations list scans
   `custom/*.conf` and `custom/*/*.conf` for `;MENU:` lines. HenWen's
   generated `henwen-sip-users*.conf` live there but carry no such line, so
   they never appear. **Fix:** a test that keeps it that way. Note the UI's
   Manual Config Edit can open those files; HenWen regenerates them, so a
   hand edit there doesn't survive.

## Overlap, page by page

| HenWen page | ASL Web UI | Overlap | Verdict |
|---|---|---|---|
| Raw rpt.conf editor | Manual Config Edit (lint, diff, snapshots, audit) | Superset | Remove |
| rpt.conf Backups / diff / restore | Change snapshots; Backups is whole-server archives | Partial, different feature | Remove once HenWen no longer writes rpt.conf |
| General Settings: node stanza | Nodes > General / Radio | Partial | Read-only, then remove |
| General Settings: macros, schedules | Raw editing only | None | Keep (decision 4) |
| Asterisk Console, verbosity, log | Asterisk > Console; Diagnostics > log | Near-exact | Remove |
| Asterisk Updates (installs, backup, confold, live log) | Check only, hands off to Cockpit | Partial | Retire (decision 2) |
| Node Access | Nodes > Access control | Exact: same AstDB `allowlist`/`denylist` keys (`nodes/access.py`) | Keep (decision 1) |
| AMI Diagnostics | Overview / Asterisk status | Partial | Keep the HenWen-credential test |
| Dashboard, Node Control, DTMF, Node ID, Smart Connector, Announcements, NWS, Recordings, Stream Relay, Discord/IRC/Meshtastic, Kiosk, TX/RX Diag, SIP identities, Users, Alerts, DVSwitch | — | None | Keep: this is the product |

## Decisions

Taken as recommended on 2026-11-11 while the owner was away; each is cheap to
reverse before W3 merges.

1. **Node Access: keep.** Both tools write the same live AstDB keys, so they
   cannot conflict, and removing it would make a club admin need a Linux
   account in `asl-web-ui` to block a node. Manager links to the UI's
   equivalent when it is installed.
2. **Asterisk Updates: retire.** ASL plus Cockpit is the supported path, and
   retiring it removes `asl-asterisk-update.sh` and two passwordless-root
   sudoers rules. The weekly "update available" notice stays and points at
   Cockpit.
3. **Require the ASL Web UI for 2.0.** 2.0 already needs HTTPS for one-call
   audio. Without this, nothing is ever deleted — the fallback has to live
   forever. Cost: Debian 13 only.
4. **Macros and schedules: keep.** Operator-facing, next to the DTMF page; the
   UI's only equivalent is editing the raw file.

## Phases

| Phase | Work | Exit |
|---|---|---|
| **W0** | Hazards 1-6: dedicated AMI user; Apache scoping script; rpt.conf stale-save guard; node-drift detector; docs for 5; test for 6 | Each fix merged. **Still owed:** install the UI on a throwaway Debian 13 + ASL3 + HenWen2 box, press every page, record what breaks. Not done on production. |
| **W1** | Detect the UI (package installed, service socket active); owner/superuser get an "Open ASL Web UI" link; duplicated pages carry a banner naming the ASL page that also does it | Link and banners shown only where the UI is installed |
| **W2** | Where the UI is installed, the node-stanza form, raw editor and backup restore become read-only with a link out; elsewhere nothing changes | On a UI box, HenWen's only rpt.conf writers are DVSwitch apply and macros/schedules |
| **W3** | Delete: raw editor, rpt.conf backups, Asterisk console, Asterisk Updates (+ script, sudoers), node-stanza form, and their tests. Rides with playbook Phase 5. Draft PR until a soak period has passed | Suite green on a smaller codebase |
| **W4** | Put the freed effort where the UI doesn't go: one-call audio, automation, integrations, recording/stream relay (playbook Phase 4), role-based accounts with TOTP for members without shell access | — |
| **Ongoing** | Watch `asl-web-ui` releases: the weekly ASL package poller also reports its version; on a new one, diff the package's `modules/*.py` list against the table above | — |
