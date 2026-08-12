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
