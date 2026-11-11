"""Unit tests for the custom rpt.conf parser (app.py, not configparser).

These are pure string-in/dict-out functions with no DB or Flask app context,
so they're tested directly against small hand-built rpt.conf fragments.
"""
import pytest

import app


SAMPLE_CONF = """\
[general]
node_lookup_method = both

[node-main](!)
duplex = 2
hangtime = 1000
idrecording = |iN8GMZ

;[daq-cham-1]
;    device = /dev/ttyUSB0
;10 = inp

[macro]
1 = *32000*32001
    ; node_lookup_method can be:
    ;    both
    ;    dns
    ;    file

[schedule]
2 = 00 00 * * *

[64393](node-main)
hangtime = 500
rxchannel = Local/64393@nodes

[64394](node-main,allscan-uci)
macro = alt-macro

[alt-macro]
1 = *31000
"""


def test_get_node_numbers():
    assert app.get_node_numbers(SAMPLE_CONF) == ["64393", "64394"]


def test_collect_stanzas_marks_template_and_ref():
    stanzas = app._collect_stanzas(SAMPLE_CONF)
    assert stanzas["node-main"]["is_template"] is True
    assert stanzas["node-main"]["template"] is None
    assert stanzas["64393"]["is_template"] is False
    assert stanzas["64393"]["template"] == "node-main"
    assert stanzas["64394"]["template"] == "node-main,allscan-uci"


def test_collect_stanzas_commented_header_is_still_a_boundary():
    """A commented-out stanza header must not let its example content bleed
    into whatever real stanza preceded it (confirmed live: [daq-cham-1]'s
    disabled ';10 = inp' example was bleeding into [macro])."""
    stanzas = app._collect_stanzas(SAMPLE_CONF)
    assert "daq-cham-1" in stanzas
    macro_lines = "\n".join(stanzas["macro"]["lines"])
    assert "10 = inp" not in macro_lines


def test_parse_kv_lines_skips_indented_doc_comments():
    kv = app._parse_kv_lines(app._collect_stanzas(SAMPLE_CONF)["macro"]["lines"])
    assert kv["1"]["value"] == "*32000*32001"
    # The heavily-indented ";    both" / ";    dns" / ";    file" lines are
    # wrapped documentation, not disabled settings, and must not appear.
    assert "both" not in kv
    assert "dns" not in kv
    assert "file" not in kv


def test_parse_kv_lines_flags_commented_setting():
    content = "[x]\n;foo = bar\n"
    stanzas = app._collect_stanzas(content)
    kv = app._parse_kv_lines(stanzas["x"]["lines"])
    assert kv["foo"] == {"value": "bar", "commented": True, "raw_line": ";foo = bar"}


def test_parse_stanza_settings_inherits_from_template():
    settings = app.parse_stanza_settings(SAMPLE_CONF, "64393")
    assert settings["duplex"]["value"] == "2"
    assert settings["duplex"]["source"] == "node-main"
    # Node's own value overrides the template's.
    assert settings["hangtime"]["value"] == "500"
    assert settings["hangtime"]["source"] == "own"
    assert settings["rxchannel"]["value"] == "Local/64393@nodes"
    assert settings["rxchannel"]["source"] == "own"


def test_parse_stanza_settings_skips_missing_template():
    """[64394](node-main,allscan-uci) references allscan-uci, which is never
    defined -- it should inherit only from node-main, not raise."""
    settings = app.parse_stanza_settings(SAMPLE_CONF, "64394")
    assert settings["duplex"]["value"] == "2"
    assert settings["duplex"]["source"] == "node-main"
    assert settings["macro"]["value"] == "alt-macro"


def test_parse_stanza_settings_unknown_stanza_returns_empty():
    assert app.parse_stanza_settings(SAMPLE_CONF, "99999") == {}


def test_section_header_match_recognizes_commented_header():
    assert app._section_header_match(";[daq-cham-1]") is not None
    assert app._section_header_match("not a header") is None


def test_get_template_names():
    assert app.get_template_names(SAMPLE_CONF) == ["node-main"]


def test_get_node_template_usage_only_lists_node_stanzas():
    usage = app.get_node_template_usage(SAMPLE_CONF)
    assert usage == {"node-main": ["64393", "64394"]}


def test_get_macro_stanza_usage_defaults_to_macro():
    usage = app.get_macro_stanza_usage(SAMPLE_CONF)
    # 64393 never sets macro=, so it defaults to the stanza named "macro".
    assert usage["macro"] == ["64393"]
    # 64394 overrides macro=alt-macro, which exists as its own stanza.
    assert usage["alt-macro"] == ["64394"]


def test_get_schedule_stanza_usage_defaults_to_schedule():
    usage = app.get_schedule_stanza_usage(SAMPLE_CONF)
    assert usage["schedule"] == ["64393", "64394"]


class TestUpdateSettingInContent:
    def test_replaces_existing_key_in_section(self):
        content = "[64393](node-main)\nhangtime = 500\n"
        out = app.update_setting_in_content(content, "64393", "hangtime", "750")
        assert "hangtime = 750\n" in out
        assert "hangtime = 500" not in out

    def test_inserts_new_key_at_end_of_section(self):
        content = "[64393](node-main)\nhangtime = 500\n[other]\nfoo = 1\n"
        out = app.update_setting_in_content(content, "64393", "totime", "180")
        stanzas = app._collect_stanzas(out)
        assert "totime" in app._parse_kv_lines(stanzas["64393"]["lines"])
        # Must not have leaked into the following section.
        assert "totime" not in app._parse_kv_lines(stanzas["other"]["lines"])

    def test_disable_comments_out_the_line(self):
        content = "[64393](node-main)\nhangtime = 500\n"
        out = app.update_setting_in_content(content, "64393", "hangtime", "500", enable=False)
        assert ";hangtime = 500\n" in out

    def test_only_touches_named_section(self):
        content = "[a]\nfoo = 1\n[b]\nfoo = 1\n"
        out = app.update_setting_in_content(content, "a", "foo", "9")
        stanzas = app._collect_stanzas(out)
        assert app._parse_kv_lines(stanzas["a"]["lines"])["foo"]["value"] == "9"
        assert app._parse_kv_lines(stanzas["b"]["lines"])["foo"]["value"] == "1"






class TestValidateMacroAndSchedule:
    def test_macro_key_must_be_numeric(self):
        assert app.validate_macro_entry("1", "*32000") is None
        assert app.validate_macro_entry("x", "*32000") is not None

    def test_macro_value_charset(self):
        assert app.validate_macro_entry("1", "*32000*32001p#ABCD") is None
        assert app.validate_macro_entry("1", "*32000;rm -rf") is not None

    def test_macro_value_blank_ok(self):
        assert app.validate_macro_entry("1", "") is None

    def test_schedule_key_must_be_numeric(self):
        assert app.validate_schedule_entry("2", "00 00 * * *") is None
        assert app.validate_schedule_entry("x", "00 00 * * *") is not None

    def test_schedule_requires_five_fields(self):
        assert app.validate_schedule_entry("2", "00 00 * *") is not None

    def test_schedule_field_only_number_or_star(self):
        assert app.validate_schedule_entry("2", "0-30 00 * * *") is not None
        assert app.validate_schedule_entry("2", "30 00 * * *") is None


def test_commented_sample_stanza_is_not_an_active_node():
    """Regression: ";[1998](node-main)" in the stock rpt.conf made guided
    DVSwitch setup refuse node 1998 as 'already exists'."""
    content = "[general]\nx=1\n;[1998](node-main)\n;morse = foo\n"
    assert "1998" in app._collect_stanzas(content)
    assert "1998" not in app._active_stanza_names(content)
    out = app.append_node_stanza(content, "1998", {"duplex": "0"})
    assert "\n[1998]\nduplex = 0\n" in out
    with pytest.raises(ValueError):
        app.append_node_stanza(out, "1998", {})


def test_ensure_loopback_nodes_entry_copies_local_iax_port():
    content = ("[nodes]\n\n643931 = radio@127.0.0.1:4570/643931,NONE\n"
               "643930 = radio@192.168.0.168:4569/643930,NONE\n;1998 = sample\n\n[node-main](!)\nx=1\n")
    out = app.ensure_loopback_nodes_entry(content, "1998", local_node="643931")
    assert "1998 = radio@127.0.0.1:4570/1998,NONE" in out
    assert out.index("1998 = radio") < out.index("[node-main]")
    # idempotent, and a commented sample doesn't count as present
    assert app.ensure_loopback_nodes_entry(out, "1998") == out


def test_ensure_loopback_nodes_entry_no_nodes_stanza_is_noop():
    assert app.ensure_loopback_nodes_entry("[general]\nx=1\n", "1998") == "[general]\nx=1\n"
