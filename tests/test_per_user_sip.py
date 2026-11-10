"""Per-user SIP identity (HenWen 2.0 Phase 2).

2.0 makes every account a real Asterisk endpoint instead of sharing 1.x's one
`henwen-tx` credential. These are the pure parts: callsign validation, the
derived Asterisk-side names, and the rendered PJSIP/dialplan config. All
testable without Asterisk, which is the reason they're pure.
"""
import os
import re

import pytest

import app

# Shaped like a real generate_sip_secret() output: URL-safe, long enough.
SECRET  = "AbCdEf0123456789-_xyzQRS"
SECRET2 = "ZyXwVu9876543210-_abcDEF"


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
        out = app.render_pjsip_user("N8GMZ", SECRET)
        for obj in ("[henwen-u-n8gmz]", "[henwen-u-n8gmz-auth]"):
            assert obj in out
        assert "type=endpoint" in out and "type=auth" in out and "type=aor" in out

    def test_webrtc_and_ulaw_only(self):
        """WebRTC mandates G.711 and app_rpt speaks it natively, so there is
        no transcoding stage and no Opus module to install."""
        out = app.render_pjsip_user("N8GMZ", SECRET)
        assert "webrtc=yes" in out
        assert "disallow=all" in out and "allow=ulaw" in out

    def test_keeps_rtp_timeout(self):
        """A browser that vanishes without a BYE otherwise leaves
        Rpt(<node>,P) up forever -- 1.x saw a 27-hour-old phone channel."""
        assert "rtp_timeout=60" in app.render_pjsip_user("N8GMZ", SECRET)

    def test_context_is_the_accounts_own(self):
        """A shared context would lose 1.x's 'a leaked credential can't dial
        anywhere else' property as soon as there were two nodes."""
        assert "context=henwen-u-n8gmz" in app.render_pjsip_user("N8GMZ", SECRET)

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
            app.render_pjsip_users([("N8GMZ", SECRET), ("n8gmz", SECRET2)])
        with pytest.raises(ValueError, match="shadows another"):
            app.render_user_dialplans([("N8GMZ", "643930"), ("N8GMZ", "1999")])

    def test_renderers_emit_every_distinct_account(self):
        out = app.render_pjsip_users([("N8GMZ", SECRET), ("KE8WNV", SECRET2)])
        assert "[henwen-u-n8gmz]" in out and "[henwen-u-ke8wnv]" in out
        assert out.lstrip().startswith("; Generated by HenWen")
        dp = app.render_user_dialplans([("N8GMZ", "643930"), ("KE8WNV", "643930")])
        assert dp.count("exten =>") == 2

    def test_empty_account_list_renders_just_the_header(self):
        out = app.render_pjsip_users([])
        assert "Do not edit by hand" in out and "type=endpoint" not in out


class TestSipSecretIsNotAConfigInjectionSink:
    """pjsip.conf is a flat key=value file, so the secret is rendered into a
    config-injection sink. generate_sip_secret() can't produce a dangerous
    value, but a secret arriving from the database, an import or a future
    caller hasn't necessarily been through it."""

    def test_generated_secrets_are_accepted(self):
        for _ in range(20):
            app.render_pjsip_user("N8GMZ", app.generate_sip_secret())

    def test_a_newline_cannot_append_config_lines(self):
        with pytest.raises(ValueError):
            app.render_pjsip_user("N8GMZ", "goodsecretgoodsecret\ncontext=default")

    def test_a_semicolon_cannot_truncate_the_password(self):
        """Asterisk would read a shortened password and the endpoint would
        exist with the wrong credential -- silently."""
        with pytest.raises(ValueError):
            app.render_pjsip_user("N8GMZ", "goodsecretgoodsecret;comment")

    def test_other_shapes_that_do_not_belong_in_a_config_value(self):
        for bad in ("short", "spaces in secret here ok", "secret\rwith-cr",
                    "sec]ret[brackets]aaaaaaaa", "a" * 200, "=equals=aaaaaaaaaaaa"):
            with pytest.raises(ValueError):
                app.render_pjsip_user("N8GMZ", bad)

    def test_multi_user_renderer_rejects_it_too(self):
        with pytest.raises(ValueError):
            app.render_pjsip_users([("N8GMZ", "goodsecretgoodsecret\nevil=1")])


class TestWriteSipUserFiles:
    """The writer. Env-overridable paths keep these away from /etc/asterisk."""

    @pytest.fixture(autouse=True)
    def _tmp_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "SIP_USERS_CONF", str(tmp_path / "sip-users.conf"))
        monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "sip-dialplan.conf"))
        self.pjsip = tmp_path / "sip-users.conf"
        self.dialplan = tmp_path / "sip-dialplan.conf"

    def test_writes_both_files(self):
        res = app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert res == {"pjsip_changed": True, "dialplan_changed": True, "accounts": 1}
        assert "[henwen-u-n8gmz]" in self.pjsip.read_text()
        assert "Rpt(643930,P,0N8GMZ)" in self.dialplan.read_text()

    def test_unchanged_content_is_not_rewritten(self):
        """What lets the caller reload Asterisk only when something really
        changed, instead of on every settings save."""
        app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        res = app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert res["pjsip_changed"] is False and res["dialplan_changed"] is False

    def test_a_change_is_detected(self):
        app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        res = app.write_sip_user_files([("N8GMZ", SECRET), ("KE8WNV", SECRET2)], "643930")
        assert res["pjsip_changed"] is True and res["accounts"] == 2

    def test_files_are_not_world_readable(self):
        """The pjsip file contains every account's SIP password. 0640 keeps
        group read, which is how the asterisk user gets at it; what must not
        be set is any 'other' bit."""
        app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        mode = self.pjsip.stat().st_mode
        assert mode & 0o007 == 0, oct(mode)
        assert mode & 0o777 == 0o640, oct(mode)

    def test_no_temp_files_are_left_behind(self):
        app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert not list(self.pjsip.parent.glob("*.tmp.*"))

    def test_a_colliding_set_writes_nothing_at_all(self):
        """Half-landing would leave one file describing endpoints the other
        file has no contexts for."""
        with pytest.raises(ValueError):
            app.write_sip_user_files([("N8GMZ", SECRET), ("n8gmz", SECRET2)], "643930")
        assert not self.pjsip.exists() and not self.dialplan.exists()

    def test_no_accounts_still_writes_a_valid_empty_file(self):
        res = app.write_sip_user_files([], "643930")
        assert res["accounts"] == 0
        assert "Do not edit by hand" in self.pjsip.read_text()


class TestSipIncludeStatus:
    """HenWen can write the generated files all day and nothing happens until
    Asterisk is wired to read them -- a one-time edit to files HenWen does not
    own. Reported rather than assumed."""

    def test_reports_missing_includes(self, tmp_path):
        pj = tmp_path / "pjsip.conf"; pj.write_text("[general]\n")
        ex = tmp_path / "extensions.conf"; ex.write_text("[general]\n")
        st = app.sip_include_status(str(pj), str(ex))
        assert st["ready"] is False
        assert st["pjsip"]["included"] is False

    def test_reports_present_includes(self, tmp_path):
        pj = tmp_path / "pjsip.conf"
        pj.write_text("[general]\n" + app.SIP_USERS_INCLUDE_LINE + "\n")
        ex = tmp_path / "extensions.conf"
        ex.write_text("[general]\n" + app.SIP_DIALPLAN_INCLUDE_LINE + "\n")
        st = app.sip_include_status(str(pj), str(ex))
        assert st["ready"] is True

    def test_an_unreadable_file_is_not_reported_as_ready(self, tmp_path):
        st = app.sip_include_status(str(tmp_path / "nope.conf"), str(tmp_path / "nope2.conf"))
        assert st["ready"] is False
        assert st["pjsip"]["readable"] is False

    def test_the_include_directive_is_tryinclude(self):
        """A missing #include target is an Asterisk config parse error, so
        wiring this up before the first provisioning run would break config
        load. tryinclude degrades to 'not there yet'."""
        assert app.SIP_USERS_INCLUDE_LINE.startswith("#tryinclude")
        assert app.SIP_DIALPLAN_INCLUDE_LINE.startswith("#tryinclude")


class TestTempFileHandling:
    """The temp file holds every account's SIP password, so how it is created
    matters as much as the final file's mode."""

    @pytest.fixture(autouse=True)
    def _tmp_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "SIP_USERS_CONF", str(tmp_path / "sip-users.conf"))
        monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "sip-dialplan.conf"))
        self.dir = tmp_path

    def test_temp_file_is_never_world_readable(self, monkeypatch):
        """open(tmp, 'w') would apply the umask and land the secrets in a 0644
        file, narrowed only afterwards. Checked at the moment of chmod, which
        is the end of that window."""
        seen = {}
        real_chmod = os.chmod

        def spy(p, m, *a, **kw):
            if ".tmp." in str(p):
                seen["mode"] = os.stat(p).st_mode & 0o777
            return real_chmod(p, m, *a, **kw)

        monkeypatch.setattr(app.os, "chmod", spy)
        app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert seen["mode"] == 0o600, oct(seen.get("mode", 0))

    def test_no_secrets_are_left_behind_when_the_write_fails(self, monkeypatch):
        """Otherwise a file full of SIP passwords sits next to the real one
        under a name nothing will ever tidy up."""
        def boom(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(app.os, "replace", boom)
        with pytest.raises(OSError):
            app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert not list(self.dir.glob("*.tmp.*")), list(self.dir.glob("*.tmp.*"))

    def test_an_existing_temp_path_is_not_clobbered(self, monkeypatch):
        """O_EXCL: the guarantee against a pre-created symlink redirecting the
        write. Forced here by pinning the random suffix so the path is known."""
        monkeypatch.setattr(app.secrets, "token_hex", lambda n: "deadbeefdeadbeef")
        victim = self.dir / f"sip-users.conf.tmp.{os.getpid()}.deadbeefdeadbeef"
        victim.write_text("pre-existing")
        with pytest.raises(FileExistsError):
            app.write_sip_user_files([("N8GMZ", SECRET)], "643930")
        assert victim.read_text() == "pre-existing"
