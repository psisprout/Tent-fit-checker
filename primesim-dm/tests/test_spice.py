import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import spice


class TestLines(unittest.TestCase):
    def test_strip_comments(self):
        self.assertEqual(spice.strip_comments("* a comment"), "")
        self.assertEqual(spice.strip_comments("R1 a b 1k $ load"), "R1 a b 1k")
        self.assertEqual(spice.strip_comments("  R1 a b 1k  "), "  R1 a b 1k")

    def test_continuation(self):
        text = ".subckt foo a b\n+ c d\n* note\nR1 a b 1k\n"
        got = list(spice.logical_lines(text))
        self.assertEqual(got[0], (1, ".subckt foo a b  c d"))
        self.assertEqual(got[1][1], "R1 a b 1k")

    def test_wrap(self):
        toks = ["X1"] + ["net%02d" % i for i in range(20)] + ["sub"]
        lines = spice.wrap(toks, width=40)
        self.assertTrue(len(lines) > 1)
        self.assertTrue(all(l.startswith("+ ") for l in lines[1:]))
        rebuilt = " ".join(lines[:1] + [l[2:] for l in lines[1:]]).split()
        self.assertEqual(rebuilt, toks)


class TestSubckt(unittest.TestCase):
    SRC = """\
* header
.subckt amp VDD VSS IN OUT
+ EN TM_A
+ param: gain=2 cl=1f
R1 IN OUT 1k
.ends amp

.SUBCKT wrap (A B)
XI A B amp
.ends
"""

    def test_parse(self):
        subs = spice.parse_subckts(self.SRC, "m.inc")
        self.assertEqual([s.name for s in subs], ["amp", "wrap"])
        amp = subs[0]
        self.assertEqual(amp.ports, ["VDD", "VSS", "IN", "OUT", "EN", "TM_A"])
        self.assertEqual(amp.params, {"gain": "2", "cl": "1f"})
        self.assertEqual(amp.line, 2)
        self.assertEqual(subs[1].ports, ["A", "B"])

    def test_spaced_equals(self):
        subs = spice.parse_subckts(".subckt f a b w = 1u\n.ends\n")
        self.assertEqual(subs[0].ports, ["a", "b"])
        self.assertEqual(subs[0].params, {"w": "1u"})

    def test_ibis_wrapper(self):
        # IBIS buffers arrive wrapped in a subckt: ports on the continuation
        # line, and the b-element carries params spaced every which way
        src = """.subckt DQ_IBIS
+ nd_in nd_out nd_pu nd_pd
b nd_pu nd_pd nd_out nd_in nd_en nd_out_of_in
+file = '.ibs'
+ model = '' type =slow buffer= input_output
+power = off
v_en nd_en 0 1
.ends
"""
        subs = spice.parse_subckts(src, "ibis.inc")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].name, "DQ_IBIS")
        self.assertEqual(subs[0].ports,
                         ["nd_in", "nd_out", "nd_pu", "nd_pd"])
        self.assertEqual(subs[0].params, {})

    def test_equals_spacing_variants(self):
        toks = spice._tokenize("b a b file = 'x.ibs' type =slow buffer= io "
                               "power=off")
        self.assertEqual(toks, ["b", "a", "b", "file='x.ibs'", "type=slow",
                                "buffer=io", "power=off"])

    def test_bus_expand(self):
        self.assertEqual(spice.expand_bus("DQ[3:0]"),
                         ["DQ[3]", "DQ[2]", "DQ[1]", "DQ[0]"])
        self.assertEqual(spice.expand_bus("A<0:2>"), ["A<0>", "A<1>", "A<2>"])
        self.assertEqual(spice.expand_bus("CLK"), ["CLK"])

    def test_bus_expand_in_subckt(self):
        subs = spice.parse_subckts(".subckt f DQ[1:0] VSS\n.ends\n",
                                   expand_buses=True)
        self.assertEqual(subs[0].ports, ["DQ[1]", "DQ[0]", "VSS"])

    def test_normalize_bus(self):
        self.assertEqual(spice.normalize_bus("DQ[3]", "angle"), "DQ<3>")
        self.assertEqual(spice.normalize_bus("DQ<3>", "bracket"), "DQ[3]")
        self.assertEqual(spice.normalize_bus("DQ(03)", "underscore"), "DQ_3")
        self.assertEqual(spice.normalize_bus("CLK", "angle"), "CLK")
        self.assertEqual(spice.normalize_bus("DQ[3]", "keep"), "DQ[3]")

    def test_nested_depth(self):
        src = ".subckt outer a\n.subckt inner b\n.ends\n.ends\n"
        subs = spice.parse_subckts(src)
        self.assertEqual([(s.name, s.depth) for s in subs],
                         [("outer", 0), ("inner", 1)])


if __name__ == "__main__":
    unittest.main()
