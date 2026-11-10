"""Per-user SIP identity (HenWen 2.0 Phase 2).

2.0 makes every account a real Asterisk endpoint instead of sharing 1.x's one
`henwen-tx` credential. These are the pure parts: callsign validation, the
derived Asterisk-side names, and the rendered PJSIP/dialplan config. All
testable without Asterisk, which is the reason they're pure.
"""
import re

import pytest

import app


class TestValidateCallsign:
    def test_normalizes_to_uppercase(self):
        assert app.validate_callsign(" n8gmz ") == "N8GMZ"

    def test_accepts_real_shapes(self):
        for cs in ("N8GMZ", "WE8CHZ", "K8DIY", "2E0ABC", "VK2XYZ", "KE8WNV"):
            assert app.validate_callsign(cs) == cs

    def test_requires_a_letter_and_a_digit(self):
        """This is what rejects the field being treated as a username."""
        for bad in ("ADMIN", "OWNER", "123456", "0000"):
            with pytest.raises(ValueError):
                app.validate_callsign(bad)

    def test_rejects_punctuation_and_ssid(self):
        """Stricter than APRS_CALLSIGN_RE on purpose: this value becomes a SIP
        username, a PJSIP object name, a dialplan context and an app_rpt link
        name, so anything beyond letters and digits is refused."""
        for bad in ("N8GMZ-7", "N8 GMZ", "N8GMZ/P", "N8;GMZ", "N8GMZ@host",
                    "../etc/passwd", "N8GMZ\nexten => 1,1,System(rm -rf /)"):
            with pytest.raises(ValueError):
                app.validate_callsign(bad)

    def test_rejects_empty_and_too_long(self):
        for bad in ("", None, "   ", "AB", "N8GMZABCDEF"):
            with pytest.raises(ValueError):
                app.validate_callsign(bad)


class TestSipIdentity:
    def test_link_name_is_zero_prefixed(self):
        """app_rpt announces any link whose name starts with a letter -- 1.x
        hit exactly that (fixed in v2026.10.28.2), and here it would happen
        once per listener."""
        assert app.sip_identity("N8GMZ")["link_name"] == "0N8GMZ"

    def test_names_are_derived_consistently(self):
        ident = app.sip_identity("n8gmz")
        assert ident["callsign"] == "N8GMZ"
        assert ident["endpoint"] == "henwen-u-n8gmz"
        assert ident["auth"]     == "henwen-u-n8gmz-auth"
        assert ident["context"]  == "henwen-u-n8gmz"

    def test_two_callsigns_never_collide(self):
        a, b = app.sip_identity("N8GMZ"), app.sip_identity("N8GMX")
        assert len(set(a.values()) & set(b.values())) == 0

    def test_invalid_callsign_is_refused_here_too(self):
        with pytest.raises(ValueError):
            app.sip_identity("ADMIN")


class TestGenerateSipSecret:
    def test_secrets_are_long_and_unique(self):
        secs = {app.generate_sip_secret() for _ in range(50)}
        assert len(secs) == 50
        assert all(len(s) >= 30 for s in secs)


class TestRenderPjsipUser:
    def test_has_the_objects_pjsip_needs(self):
        out = app.render_pjsip_user("N8GMZ", "s3cr3t")
        for obj in ("[henwen-u-n8gmz]", "[henwen-u-n8gmz-auth]"):
            assert obj in out
        assert "type=endpoint" in out and "type=auth" in out and "type=aor" in out

    def test_webrtc_and_ulaw_only(self):
        """WebRTC mandates G.711 and app_rpt speaks it natively, so there is
        no transcoding stage and no Opus module to install."""
        out = app.render_pjsip_user("N8GMZ", "s3cr3t")
        assert "webrtc=yes" in out
        assert "disallow=all" in out and "allow=ulaw" in out

    def test_keeps_rtp_timeout(self):
        """A browser that vanishes without a BYE otherwise leaves
        Rpt(<node>,P) up forever -- 1.x saw a 27-hour-old phone channel."""
        assert "rtp_timeout=60" in app.render_pjsip_user("N8GMZ", "s3cr3t")

    def test_context_is_the_accounts_own(self):
        """A shared context would lose 1.x's 'a leaked credential can't dial
        anywhere else' property as soon as there were two nodes."""
        assert "context=henwen-u-n8gmz" in app.render_pjsip_user("N8GMZ", "x")

    def test_secret_is_required(self):
        for bad in ("", None, "   "):
            with pytest.raises(ValueError):
                app.render_pjsip_user("N8GMZ", bad)


class TestRenderUserDialplan:
    def test_exposes_exactly_one_extension(self):
        out = app.render_user_dialplan("N8GMZ", "643930")
        assert out.count("exten =>") == 1
        assert "exten => 643930,1,Answer()" in out

    def test_uses_phone_control_mode_and_the_link_name(self):
        out = app.render_user_dialplan("N8GMZ", 643930)
        assert "Rpt(643930,P,0N8GMZ)" in out

    def test_node_must_be_numeric(self):
        for bad in ("643930; System(rm -rf /)", "abc", "", None):
            with pytest.raises((ValueError, TypeError)):
                app.render_user_dialplan("N8GMZ", bad)

    def test_nothing_unescaped_reaches_the_dialplan(self):
        """Both inputs are validated rather than escaped, so the rendered
        output can only ever contain the shapes below."""
        out = app.render_user_dialplan("n8gmz", "1999")
        for line in out.splitlines():
            assert re.match(r'^(\[[a-z0-9\-]+\]|; .*|exten => \d+,1,Answer\(\)|'
                            r' same => n,(Rpt\(\d+,P,0[A-Z0-9]+\)|Hangup\(\)))$', line), line


class TestSchema:
    def test_users_table_has_the_new_columns(self, fresh_db):
        cols = {r[1] for r in app.get_db().execute("PRAGMA table_info(users)").fetchall()}
        assert {"callsign", "sip_secret"} <= cols


class TestCallsignUniqueness:
    """One callsign, one account. sip_identity() derives the endpoint, auth,
    aor and context from the callsign alone, so two accounts sharing one would
    share a single SIP identity -- same credential, revoked together,
    indistinguishable in `rpt lstats`. That is the opposite of the per-user
    attribution this design rests on, so it is guarded twice: in the schema,
    and again in the renderer."""

    def test_schema_rejects_a_duplicate_callsign(self, fresh_db):
        db = app.get_db()
        db.execute("INSERT INTO users (username, password_hash, callsign) VALUES (?,?,?)",
                   ("a", "x", "N8GMZ"))
        db.commit()
        with pytest.raises(Exception):
            db.execute("INSERT INTO users (username, password_hash, callsign) VALUES (?,?,?)",
                       ("b", "x", "N8GMZ"))
            db.commit()

    def test_schema_rejects_a_differently_cased_duplicate(self, fresh_db):
        """validate_callsign() uppercases, but a direct DB write need not have
        gone through it -- hence COLLATE NOCASE on the index."""
        db = app.get_db()
        db.execute("INSERT INTO users (username, password_hash, callsign) VALUES (?,?,?)",
                   ("a", "x", "N8GMZ"))
        db.commit()
        with pytest.raises(Exception):
            db.execute("INSERT INTO users (username, password_hash, callsign) VALUES (?,?,?)",
                       ("b", "x", "n8gmz"))
            db.commit()

    def test_many_accounts_may_have_no_callsign(self, fresh_db):
        """Every account predating Phase 2 is in this state."""
        db = app.get_db()
        for name in ("a", "b", "c"):
            db.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (name, "x"))
        db.commit()
        assert db.execute("SELECT count(*) FROM users WHERE callsign IS NULL").fetchone()[0] == 3

    def test_renderers_fail_closed_on_a_duplicate(self):
        """Rendering a file where one account silently shadows another would be
        worse than rendering nothing."""
        with pytest.raises(ValueError, match="shadows another"):
            app.render_pjsip_users([("N8GMZ", "s1"), ("n8gmz", "s2")])
        with pytest.raises(ValueError, match="shadows another"):
            app.render_user_dialplans([("N8GMZ", "643930"), ("N8GMZ", "1999")])

    def test_renderers_emit_every_distinct_account(self):
        out = app.render_pjsip_users([("N8GMZ", "s1"), ("KE8WNV", "s2")])
        assert "[henwen-u-n8gmz]" in out and "[henwen-u-ke8wnv]" in out
        assert out.lstrip().startswith("; Generated by HenWen")
        dp = app.render_user_dialplans([("N8GMZ", "643930"), ("KE8WNV", "643930")])
        assert dp.count("exten =>") == 2

    def test_empty_account_list_renders_just_the_header(self):
        out = app.render_pjsip_users([])
        assert "Do not edit by hand" in out and "type=endpoint" not in out
