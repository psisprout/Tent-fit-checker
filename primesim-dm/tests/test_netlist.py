import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import config, emit, netlist, spice

MODEL = """\
.subckt cell VDD VSS IN[1:0] OUT[1:0] EN TM_X SPARE0
R1 IN[0] OUT[0] 1k
.ends
"""


def build(overrides, model=MODEL, expand=True):
    raw = {
        "deck": {"title": "t", "output": "o.sp"},
        "models": {"files": [], "expand_buses": expand},
        "instances": [{"name": "X1", "subckt": "cell"}],
    }
    raw = config._merge(raw, overrides)
    cfg = config.normalize(raw)
    subs = spice.parse_subckts(model, "mem.inc",
                               expand_buses=cfg["models"]["expand_buses"])
    r = netlist.Resolver(cfg, netlist.build_index(subs))
    return cfg, r.build()


def nets_of(nl, inst="X1"):
    for i, _s, bindings in nl.instances:
        if i["name"] == inst:
            return dict((b.port, b.net) for b in bindings)
    raise AssertionError("no instance " + inst)


class TestTemplate(unittest.TestCase):
    def test_groups_and_transforms(self):
        m = re.match(r"^V(DD|SS)Q$", "VDDQ")
        ctx = {"port": "VDDQ", "inst": "X1"}
        self.assertEqual(netlist.expand_template("v{1|lower}q", m, ctx), "vddq")
        self.assertEqual(netlist.expand_template("{inst}_{port}", m, ctx),
                         "X1_VDDQ")
        m2 = re.match(r"^DQ<(\d+)>$", "DQ<7>")
        self.assertEqual(netlist.expand_template("d{1|zfill:3}", m2, {}), "d007")

    def test_unknown_field(self):
        self.assertRaises(netlist.NetlistError,
                          netlist.expand_template, "{nope}", None, {})


CHANNEL = """\
*node 1  SCA0_A_BGA1_G4_T1
*node 2  SCA0_A_DIE1_46_T1
*node 3  SCA0_A_DIE2_46_T1
*node 4  SA1_A_BGA1_G5_T1
.subckt ch_sp 1 2 3 4
R1 1 2 0.1
.ends
"""

ALIAS_SIG = {"sig": {"on_miss": "error", "rules": [
    {"match": "^S?CA(\\d+)$", "to": "ca{1}"},
    {"match": "^SA(\\d+)$", "to": "ca{1}"}]}}

CHANNEL_RULES = [
    {"match": "^(\\w+?)_\\w+_BGA\\d*_.*", "net": "rcv_ball_{1|alias:sig}"},
    {"match": "^(\\w+?)_\\w+_DIE(\\d+)_.*", "net": "rcv{2}_bump_{1|alias:sig}"},
]


class TestChannelAlias(unittest.TestCase):
    """S-parameter channel: names come from *node, signal names vary."""

    def build_ch(self, aliases=ALIAS_SIG):
        return build({"aliases": aliases,
                      "naming": {"case": "lower", "default": "error",
                                 "rules": CHANNEL_RULES},
                      "instances": [{"name": "XCH", "subckt": "ch_sp"}]},
                     model=CHANNEL, expand=False)

    def test_alias_and_die_index(self):
        _cfg, nl = self.build_ch()
        got = nets_of(nl, "XCH")
        self.assertEqual(got["SCA0_A_BGA1_G4_T1"], "rcv_ball_ca0")
        self.assertEqual(got["SCA0_A_DIE1_46_T1"], "rcv1_bump_ca0")
        self.assertEqual(got["SCA0_A_DIE2_46_T1"], "rcv2_bump_ca0")
        self.assertEqual(got["SA1_A_BGA1_G5_T1"], "rcv_ball_ca1")

    def test_unknown_signal_name_is_an_error(self):
        aliases = {"sig": {"on_miss": "error",
                           "rules": [{"match": "^ZZ(\\d+)$", "to": "zz{1}"}]}}
        try:
            self.build_ch(aliases)
        except netlist.NetlistError as exc:
            self.assertIn("SCA0", str(exc))
            self.assertIn("sig", str(exc))
        else:
            self.fail("an unmapped signal name should not pass silently")

    def test_on_miss_keep_passes_the_name_through(self):
        aliases = {"sig": {"on_miss": "keep",
                           "rules": [{"match": "^ZZ(\\d+)$", "to": "zz{1}"}]}}
        _cfg, nl = self.build_ch(aliases)
        self.assertEqual(nets_of(nl, "XCH")["SCA0_A_BGA1_G4_T1"],
                         "rcv_ball_sca0")

    def test_undefined_alias_table(self):
        self.assertRaises(netlist.NetlistError, self.build_ch, {})


class TestResolution(unittest.TestCase):
    def test_default_same_name(self):
        _cfg, nl = build({})
        self.assertEqual(nets_of(nl)["VDD"], "VDD")

    def test_case_and_bus_style(self):
        _cfg, nl = build({"naming": {"case": "lower", "bus_style": "angle"}})
        n = nets_of(nl)
        self.assertEqual(n["VDD"], "vdd")
        self.assertEqual(n["IN[0]"], "in<0>")

    def test_rule_matches_normalized_port(self):
        # ports are declared with [], the rule is written with <>
        _cfg, nl = build({
            "naming": {"bus_style": "angle", "case": "lower",
                       "rules": [{"match": r"^IN<(\d+)>$", "net": "din<{1}>"}]},
        })
        self.assertEqual(nets_of(nl)["IN[1]"], "din<1>")

    def test_precedence_explicit_beats_rule(self):
        _cfg, nl = build({
            "naming": {"rules": [{"match": "^EN$", "net": "from_rule"}]},
            "instances": [{"name": "X1", "subckt": "cell",
                           "connect": {"EN": "from_connect"}}],
        })
        self.assertEqual(nets_of(nl)["EN"], "from_connect")

    def test_instance_rule_beats_global_rule(self):
        _cfg, nl = build({
            "naming": {"rules": [{"match": "^EN$", "net": "global_net"}]},
            "instances": [{"name": "X1", "subckt": "cell",
                           "rules": [{"match": "^EN$", "net": "inst_net"}]}],
        })
        self.assertEqual(nets_of(nl)["EN"], "inst_net")

    def test_connect_is_case_insensitive(self):
        _cfg, nl = build({"instances": [{"name": "X1", "subckt": "cell",
                                         "connect": {"en": "e"}}]})
        self.assertEqual(nets_of(nl)["EN"], "e")

    def test_connect_typo_warns(self):
        _cfg, nl = build({"instances": [{"name": "X1", "subckt": "cell",
                                         "connect": {"ENABLE": "e"}}]})
        self.assertTrue(any("ENABLE" in w for w in nl.warnings))

    def test_literal_escape(self):
        _cfg, nl = build({"naming": {"case": "lower"},
                          "instances": [{"name": "X1", "subckt": "cell",
                                         "connect": {"EN": "!KeepCase"}}]})
        self.assertEqual(nets_of(nl)["EN"], "KeepCase")

    def test_missing_subckt_raises(self):
        self.assertRaises(netlist.NetlistError, build,
                          {"instances": [{"name": "X1", "subckt": "nope"}]})

    def test_default_error_policy(self):
        self.assertRaises(netlist.NetlistError, build,
                          {"naming": {"default": "error"}})


class TestVersionedModels(unittest.TestCase):
    """Stage folders hold several DB versions of the same subckt."""

    SRC = ".subckt pkg_sp a b\nR1 a b 0.2\n.ends\n"

    def build_two(self, inst_extra=None, on_dup="error"):
        raw = {"models": {"on_duplicate": on_dup},
               "naming": {"default": "same_name"},
               "instances": [dict({"name": "XPKG", "subckt": "pkg_sp"},
                                  **(inst_extra or {}))]}
        cfg = config.normalize(raw)
        subs = (spice.parse_subckts(self.SRC, "/m/2_pkg/pkg_v1p0.inc")
                + spice.parse_subckts(self.SRC, "/m/2_pkg/pkg_v2p3.inc"))
        return netlist.Resolver(cfg, netlist.build_index(subs)).build()

    def test_ambiguous_version_is_an_error(self):
        try:
            self.build_two()
        except netlist.NetlistError as exc:
            self.assertIn("pkg_v1p0.inc", str(exc))
            self.assertIn("pkg_v2p3.inc", str(exc))
            self.assertIn("source", str(exc))
        else:
            self.fail("picking a DB version must not happen silently")

    def test_source_pins_the_version(self):
        nl = self.build_two({"source": "pkg_v2p3.inc"})
        self.assertEqual(nl.instances[0][1].path, "/m/2_pkg/pkg_v2p3.inc")

    def test_source_that_still_matches_both_is_an_error(self):
        self.assertRaises(netlist.NetlistError, self.build_two,
                          {"source": "2_pkg"})

    def test_source_matching_nothing_is_an_error(self):
        self.assertRaises(netlist.NetlistError, self.build_two,
                          {"source": "pkg_v9.inc"})

    def test_warn_policy_keeps_the_old_behaviour(self):
        nl = self.build_two(on_dup="warn")
        self.assertTrue(any("defined in 2 files" in w for w in nl.warnings))
        self.assertEqual(nl.instances[0][1].path, "/m/2_pkg/pkg_v1p0.inc")

    def test_unused_duplicate_does_not_complain(self):
        # only an instance that actually references the name has a problem
        raw = {"naming": {"default": "same_name"},
               "instances": [{"name": "XA", "subckt": "other"}]}
        cfg = config.normalize(raw)
        subs = (spice.parse_subckts(self.SRC, "/m/a.inc")
                + spice.parse_subckts(self.SRC, "/m/b.inc")
                + spice.parse_subckts(".subckt other a b\nR1 a b 1k\n.ends\n",
                                      "/m/c.inc"))
        nl = netlist.Resolver(cfg, netlist.build_index(subs)).build()
        self.assertEqual([w for w in nl.warnings if "pkg_sp" in w], [])


class TestTermination(unittest.TestCase):
    def test_tie_uses_the_rail_directly(self):
        _cfg, nl = build({"naming": {"rules": [
            {"match": "^TM_", "action": "terminate", "type": "tie", "to": "VSS"}]}})
        self.assertEqual(nets_of(nl)["TM_X"], "VSS")
        # a tie needs no element of its own
        self.assertEqual([e for e in nl.term_elements if "TM_X" in e.comment],
                         [])

    def test_rload_adds_a_resistor(self):
        _cfg, nl = build({
            "termination": {"default": {"type": "rload", "to": "VSS",
                                        "value": "1T"}},
            "instances": [{"name": "X1", "subckt": "cell", "unused": ["^TM_"]}],
        })
        els = [e for e in nl.term_elements if "TM_X" in e.comment]
        self.assertEqual(len(els), 1)
        self.assertEqual(els[0].kind, "R")
        self.assertEqual(els[0].nodes[1], "VSS")
        self.assertEqual(els[0].value, "1T")

    def test_override_by_port_pattern(self):
        _cfg, nl = build({
            "termination": {
                "default": {"type": "rload", "to": "VSS", "value": "1T"},
                "overrides": [{"match": "^TM_", "type": "cload", "to": "VSS",
                               "value": "2f"}]},
            "instances": [{"name": "X1", "subckt": "cell", "unused": ["^TM_"]}],
        })
        els = [e for e in nl.term_elements if "TM_X" in e.comment]
        self.assertEqual((els[0].kind, els[0].value), ("C", "2f"))

    def test_open_is_not_reported_as_floating(self):
        _cfg, nl = build({"naming": {"rules": [
            {"match": "^SPARE", "action": "terminate", "type": "open"}]}})
        self.assertEqual([w for w in nl.warnings if "SPARE" in w], [])
        self.assertTrue(nets_of(nl)["SPARE0"] in nl.open_nets)

    def test_floating_net_is_auto_terminated(self):
        # OUT[1] is driven by nothing else in this one-instance deck
        _cfg, nl = build({"termination": {"auto_terminate_floating": True,
                                          "default": {"type": "rload",
                                                      "to": "VSS",
                                                      "value": "1T"}}})
        touched = set(e.comment for e in nl.term_elements)
        self.assertIn("X1.OUT[1]", touched)
        # nothing in the config anticipated it, so it is still flagged
        self.assertTrue(any("OUT[1]" in w for w in nl.warnings))

    def test_anticipated_auto_term_is_quiet(self):
        # an override saying "outputs get a cap" means we meant it
        _cfg, nl = build({"termination": {
            "auto_terminate_floating": True,
            "default": {"type": "rload", "to": "VSS", "value": "1T"},
            "overrides": [{"match": "^OUT", "type": "cload", "to": "VSS",
                           "value": "5f"}]}})
        self.assertEqual([w for w in nl.warnings if "OUT[" in w], [])
        els = [e for e in nl.term_elements if e.comment == "X1.OUT[1]"]
        self.assertEqual(els[0].kind, "C")

    def test_floating_can_be_left_alone(self):
        _cfg, nl = build({"termination": {"auto_terminate_floating": False}})
        self.assertTrue(any("floating net" in w for w in nl.warnings))

    def test_auto_term_never_shorts_to_a_rail(self):
        # a 'tie' default would short a real signal, so the auto pass downgrades
        _cfg, nl = build({"termination": {"default": {"type": "tie",
                                                      "to": "VSS"}}})
        els = [e for e in nl.term_elements if e.comment == "X1.OUT[1]"]
        self.assertEqual(len(els), 1)
        self.assertEqual(els[0].kind, "R")

    def test_keep_nets_suppresses_the_check(self):
        _cfg, nl = build({"termination": {"auto_terminate_floating": False,
                                          "keep_nets": ["^OUT", "^SPARE",
                                                        "^IN", "^EN", "^TM_",
                                                        "^V"]}})
        self.assertEqual(nl.warnings, [])


class TestEmit(unittest.TestCase):
    def test_deck_has_ports_in_subckt_order(self):
        cfg, nl = build({"naming": {"case": "lower", "bus_style": "angle"}})
        deck = emit.render_deck(cfg, nl)
        line = [l for l in deck.splitlines() if l.startswith("X1 ")][0]
        toks = line.split()
        self.assertEqual(toks[1:5], ["vdd", "vss", "in<1>", "in<0>"])
        self.assertIn("cell", toks)

    def test_report_lists_every_port(self):
        cfg, nl = build({})
        rep = emit.render_report(cfg, nl, ["mem.inc"])
        for port in ("VDD", "VSS", "IN[1]", "OUT[0]", "EN", "TM_X", "SPARE0"):
            self.assertIn(port, rep)


if __name__ == "__main__":
    unittest.main()
