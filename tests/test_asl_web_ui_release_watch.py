"""The weekly ASL package check also watches asl-web-ui, so the overlap map
in docs/ASL-WEB-UI-PLAN.md is re-read when a newer UI ships."""
import shutil
import subprocess

import pytest

import app

HAVE_DPKG = shutil.which("dpkg") is not None


def _stub(monkeypatch, installed, candidate):
    real = app._asl_run

    def run(cmd, timeout):
        if cmd[:2] == ["apt-cache", "policy"] and cmd[2:] == [app.ASL_WEB_UI_PKG]:
            out = (f"{app.ASL_WEB_UI_PKG}:\n  Installed: {installed or '(none)'}\n"
                   f"  Candidate: {candidate or '(none)'}\n")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[0] == "dpkg":
            return real(cmd, timeout)
        raise AssertionError(cmd)
    monkeypatch.setattr(app, "_asl_run", run)


@pytest.mark.skipif(not HAVE_DPKG, reason="needs dpkg --compare-versions")
class TestVersions:
    def test_reviewed_version_needs_nothing(self, monkeypatch):
        _stub(monkeypatch, app.ASL_WEB_UI_REVIEWED_VERSION, app.ASL_WEB_UI_REVIEWED_VERSION)
        v = app._asl_web_ui_versions()
        assert v["needs_review"] is False and v["newest"] is None

    def test_newer_candidate_needs_review(self, monkeypatch):
        _stub(monkeypatch, None, "1.1-1.deb13")
        v = app._asl_web_ui_versions()
        assert v["needs_review"] is True and v["newest"] == "1.1-1.deb13"

    def test_newer_installed_needs_review(self, monkeypatch):
        _stub(monkeypatch, "1.0-2.deb13", "1.0-2.deb13")
        assert app._asl_web_ui_versions()["needs_review"] is True

    def test_older_or_absent_does_not(self, monkeypatch):
        _stub(monkeypatch, None, "0.9-1.deb13")
        assert app._asl_web_ui_versions()["needs_review"] is False
        _stub(monkeypatch, None, None)
        assert app._asl_web_ui_versions()["needs_review"] is False


def test_check_records_it(fresh_db, monkeypatch):
    monkeypatch.setattr(app, "_asl_collect_update_info", lambda: {
        "packages": [], "will_upgrade": [], "available": False, "error": ""})
    monkeypatch.setattr(app, "_asl_web_ui_versions", lambda: {
        "installed": None, "candidate": "9.9", "reviewed": "1.0", "needs_review": True, "newest": "9.9"})
    state = app._asl_check_for_updates(refresh=False)
    assert state["asl_web_ui"]["needs_review"] is True
    assert app._asl_load_state()["asl_web_ui"]["newest"] == "9.9"


def test_a_failure_there_does_not_break_the_check(fresh_db, monkeypatch):
    monkeypatch.setattr(app, "_asl_collect_update_info", lambda: {
        "packages": [], "will_upgrade": [], "available": False, "error": ""})
    def boom():
        raise OSError("apt-cache missing")
    monkeypatch.setattr(app, "_asl_web_ui_versions", boom)
    state = app._asl_check_for_updates(refresh=False)
    assert state["asl_web_ui"] == {"error": "apt-cache missing"}
