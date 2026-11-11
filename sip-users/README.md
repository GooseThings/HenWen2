# Per-user SIP identities (HenWen 2.0)

Every account that has a **callsign** (Manager > Users) becomes a real
Asterisk endpoint of its own instead of sharing the 1.x `henwen-tx`
credential. HenWen renders those accounts into two generated files and
reloads Asterisk whenever they change:

| File | Contents |
|---|---|
| `/etc/asterisk/custom/henwen-sip-users.conf` | one PJSIP endpoint + auth + aor per account (`henwen-u-<callsign>`) |
| `/etc/asterisk/custom/henwen-sip-users-dialplan.conf` | one context per account exposing exactly its own node, in phone-control mode, under the app_rpt link name `0<CALLSIGN>` |

Asterisk only reads them once `pjsip.conf` and `extensions.conf` each carry
a `#tryinclude` for the matching file. That is a one-time edit to files
HenWen does not own, so it is done by `apply.sh` here (Manager > TX
Diagnostics > "Per-user SIP" card, or `sudo bash sip-users/apply.sh`), the
same way `tx-spike/apply.sh` installs the shared endpoint. `rollback.sh`
removes the lines again.

**Prerequisite:** `tx-spike/apply.sh` has run. The per-user endpoints reuse
its WebSocket transport, modules, STUN setting and Apache `/asterisk-ws`
proxy; they replace only the credential.

**What a session gets:** `GET /api/tx/config` hands a logged-in account its
own credential when it has one *and* the includes are wired, otherwise the
shared `henwen-tx` one. The response says which (`identity: user|shared`).
The SIP user (`username`, the From header) is the endpoint name; the auth
username (`auth_user`) is the callsign -- PJSIP's `username` identifier
matches the From user against the endpoint *name*, so the two differ on
purpose and the browser passes the second as JsSIP's `authorization_user`.

**Revocation:** the secret is regenerated on every password change (admin-
set, self-service, or a completed reset) and on a callsign change; it is
removed when the callsign is cleared or the account deleted. Each of those
re-renders the files, reloads, and hangs up any call that endpoint has up --
a reload alone does not end a call already in progress.

Reload commands are `module reload res_pjsip.so` and `dialplan reload`
(`pjsip reload` is not a CLI command on Asterisk 22 / ASL3). Neither
restarts anything or touches other calls.
