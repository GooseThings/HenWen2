# Code scanning triage (2026-11-10)

CodeQL and OSSAR started reporting on this repo the moment it was made
public, and surfaced **86 open alerts**. All 86 are in code inherited from
HenWen 1.x — none are in the 2.0 work. This file records the disposition of
each group so it is decided once rather than re-argued every time someone
opens the Security tab.

| count | rule | where | disposition |
|---|---|---|---|
| 69 | `py/stack-trace-exposure` | app.py | **Won't fix** — every site is admin-gated. See below. |
| 9 | `py/path-injection` | app.py | **False positive** — sanitized twice. |
| 5 | `py/incomplete-url-substring-sanitization` | 4 tests, 1 app.py | **False positive** — the check is anchored. |
| 2 | `py/weak-sensitive-data-hashing` | app.py | **False positive** — sha256 over random tokens, not passwords. |
| 1 | `py/clear-text-logging-sensitive-data` | app.py | **False positive** — only dict keys are logged. |

**Why dispositioning them matters more than the individual verdicts:** 86
standing alerts means the 87th — a real one, in new code — arrives invisible.
A clean dashboard is the point.

## `py/stack-trace-exposure` ×69 — won't fix

The pattern is `return jsonify({"error": str(e)}), 500`, at 42 sites.

Checked every one of those 42 against `check_auth()`'s own sets: **all 42 are
in admin+-gated routes. None is public, and none is reachable by a plain
`user`/kiosk account.** So the only audience for the exception text is an
admin or the owner — someone who can read `journalctl -u HenWen2` anyway, and
for whom that text *is* the diagnostic. The rule is about leaking internals to
untrusted users; there is no untrusted user here.

The alternative — genericize all 42 and log the detail — would make the
operator's own error reporting materially worse to satisfy a rule whose threat
model does not apply. If a route ever moves into `_PUBLIC` or
`_USER_OR_ABOVE`, its handler needs revisiting; that is the thing to watch,
not the count.

Reproduce the check:

```bash
python3 - <<'EOF'
# prints each str(e) site with the auth bucket of its enclosing route
EOF
```
(the script is in the PR #1 discussion; it parses `_PUBLIC` / `_USER_OR_ABOVE`
out of `check_auth()` and maps each site's enclosing `def`.)

## `py/path-injection` ×9 — false positive

Sinks are `os.makedirs`/`os.chmod`/`shutil.chown`/`os.replace` on a `dest`
built by `_ann_sound_path()`. The slug reaching it is sanitized twice:

1. `_ann_slug()` reduces the user-supplied name with
   `re.sub(r'[^a-zA-Z0-9_-]', '_', ...)` — no `/`, no `.`, so no traversal
   component survives.
2. `_ann_sound_path()` then applies `os.path.basename()` at the actual
   `os.path.join()`, deliberately, per its own comment: "makes that a hard
   local guarantee rather than something every future caller must remember".

CodeQL models neither barrier. Escaping `SOUNDS_DIR` is not reachable.

## `py/incomplete-url-substring-sanitization` ×5 — false positive

The rule targets `"discord.com" in url`-style checks, which `evil.com/discord.com`
defeats. The actual code is anchored:

```python
if webhook_url and not webhook_url.startswith("https://discord.com/api/webhooks/") \
        and not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
```

A full scheme+host+path prefix via `startswith` is the correct form. Four of
the five alerts are in `tests/test_alerts.py` anyway.

## `py/weak-sensitive-data-hashing` ×2 — false positive

sha256 over password-reset tokens, invite tokens and 2FA recovery codes —
all high-entropy random values, where there is nothing to brute force and a
fast hash is appropriate. **Passwords** use werkzeug's scrypt via
`generate_password_hash`, plus an optional `PASSWORD_PEPPER`; that is a
different code path and is not what these alerts point at.

## `py/clear-text-logging-sensitive-data` ×1 — false positive

Sink is `print(out)` inside `log()`. The flagged flow is the `/api/users`
update route, which logs only the **keys** of the submitted dict and
explicitly drops the password:

```python
log("INFO", f"[USERS] Updated user id={uid}: {', '.join(k for k in data if k != 'password')}")
```

No password value reaches `log()` anywhere in the file — every
credential-adjacent log line records a username, a role or an event.
