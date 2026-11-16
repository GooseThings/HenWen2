"""Release-version parsing and comparison (CHANGELOG heading, update check).

HenWen 2.x versions are semver with an optional pre-release suffix
(v2.0.0-beta.1); the 1.x history in the same CHANGELOG is CalVer
(v2026.10.30). A plain string compare ranks every 1.x tag above every 2.x
one, which is how an update notice pointing back at 1.x would appear.
"""
import app


class TestVersionHeading:
    def test_semver_prerelease_heading(self):
        assert app._VERSION_HEADING_RE.match("## v2.0.0-beta.1").group(1) == "v2.0.0-beta.1"

    def test_semver_release_heading(self):
        assert app._VERSION_HEADING_RE.match("## v2.1.0").group(1) == "v2.1.0"

    def test_calver_headings_still_match(self):
        assert app._VERSION_HEADING_RE.match("## v2026.10.30").group(1) == "v2026.10.30"
        assert app._VERSION_HEADING_RE.match("## v2026.10.28.2").group(1) == "v2026.10.28.2"

    def test_prose_headings_do_not_match(self):
        assert app._VERSION_HEADING_RE.match("## HenWen 2.0 — unreleased") is None
        assert app._VERSION_HEADING_RE.match("## 1.x history") is None

    def test_changelog_top_version_is_readable(self):
        assert app._version_key(app._load_henwen_version()) is not None


class TestVersionNewer:
    def test_prerelease_is_older_than_its_release(self):
        assert app._version_newer("v2.0.0", "v2.0.0-beta.1")
        assert not app._version_newer("v2.0.0-beta.1", "v2.0.0")

    def test_prerelease_numbers_compare_numerically(self):
        assert app._version_newer("v2.0.0-beta.10", "v2.0.0-beta.9")
        assert app._version_newer("v2.0.0-beta.2", "v2.0.0-beta.1")

    def test_rc_is_newer_than_beta(self):
        assert app._version_newer("v2.0.0-rc.1", "v2.0.0-beta.3")

    def test_numeric_parts_compare_numerically(self):
        assert app._version_newer("v2.0.10", "v2.0.9")
        assert app._version_newer("v2.1.0", "v2.0.9")

    def test_same_version_is_not_newer(self):
        assert not app._version_newer("v2.0.0-beta.1", "v2.0.0-beta.1")

    def test_unreadable_tags_never_report_an_update(self):
        assert not app._version_newer("", "v2.0.0-beta.1")
        assert not app._version_newer("nightly", "v2.0.0-beta.1")
        assert not app._version_newer("v2.0.0", "unknown")


def _login_owner(client, create_user):
    row = create_user("owner1", role="owner")
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="owner1", role="owner", user_id=row["id"],
                    password_epoch=row["password_epoch"])


def test_update_check_reports_no_update_without_a_published_release(client, create_user, monkeypatch):
    _login_owner(client, create_user)
    monkeypatch.setattr(app, "get_latest_release", lambda: {"tag": "", "url": ""})
    d = client.get("/api/update-check").get_json()
    assert d["update_available"] is False
    assert d["url"].endswith("GooseThings/HenWen2/releases/latest")


def test_update_check_reports_a_newer_release(client, create_user, monkeypatch):
    _login_owner(client, create_user)
    monkeypatch.setattr(app, "HENWEN_VERSION", "v2.0.0-beta.1")
    monkeypatch.setattr(app, "get_latest_release", lambda: {"tag": "v2.0.0", "url": "x"})
    assert client.get("/api/update-check").get_json()["update_available"] is True
