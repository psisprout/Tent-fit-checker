import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import check, deck


class Harness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def lint(self, text, name="d.sp", **kw):
        path = self.write(name, text)
        dk = deck.read([path], search_dirs=[self.dir])
        c = check.Checker(dk, **kw)
        return dk, c, c.run()

    def codes(self, findings):
        return [f.code for f in findings]


class TestNodeSplitting(Harness):
    def nodes_of(self, line, name):
        dk, _c, _f = self.lint(line + "\n")
        for el in dk.elements:
            if el.name.lower() == name.lower():
                return el.nodes
        raise AssertionError("no element %s (unparsed: %s)"
                             % (name, dk.unparsed))

    def test_two_terminal(self):
        self.assertEqual(self.nodes_of("R1 a b 1k", "R1"), ["a", "b"])
        self.assertEqual(self.nodes_of("C1 a b 1f", "C1"), ["a", "b"])
        self.assertEqual(self.nodes_of("V1 a 0 DC 1.1", "V1"), ["a", "0"])

    def test_subckt_call(self):
        self.assertEqual(self.nodes_of("X1 a b c mysub p=1", "X1"),
                         ["a", "b", "c"])

    def test_mosfet_keeps_four_nodes(self):
        self.assertEqual(self.nodes_of("M1 nd ng ns nb nch w=1u l=30n", "M1"),
                         ["nd", "ng", "ns", "nb"])

    def test_bjt_three_or_four(self):
        self.assertEqual(self.nodes_of("Q1 nc nb ne npn", "Q1"),
                         ["nc", "nb", "ne"])
        self.assertEqual(self.nodes_of("Q2 nc nb ne ns npn", "Q2"),
                         ["nc", "nb", "ne", "ns"])

    def test_s_element_stops_at_first_param(self):
        self.assertEqual(self.nodes_of("S1 n1 n2 n3 n4 mname=smod", "S1"),
                         ["n1", "n2", "n3", "n4"])

    def test_ibis_b_element(self):
        self.assertEqual(
            self.nodes_of("b nd_pu nd_pd nd_out nd_in nd_en nd_o "
                          "file='x.ibs' type =slow power = off", "b"),
            ["nd_pu", "nd_pd", "nd_out", "nd_in", "nd_en", "nd_o"])

    def test_w_element_uses_N(self):
        self.assertEqual(
            self.nodes_of("W1 i1 i2 iref o1 o2 oref N=2 RLGCfile='x.rlgc'",
                          "W1"),
            ["i1", "i2", "iref", "o1", "o2", "oref"])

    def test_unsplittable_line_is_recorded_not_guessed(self):
        dk, _c, _f = self.lint("Yweird a b c d\n")
        self.assertEqual(dk.elements, [])
        self.assertEqual(len(dk.unparsed), 1)
        self.assertIn("Yweird", dk.unparsed[0][2])

    def test_elements_inside_subckt_are_not_top_level(self):
        dk, _c, _f = self.lint(
            ".subckt s a b\nR1 a b 1k\n.ends\nR2 x y 1k\n")
        self.assertEqual([e.name for e in dk.elements], ["R2"])


class TestIncludeFollowing(Harness):
    def test_relative_include_is_followed(self):
        os.makedirs(os.path.join(self.dir, "models"))
        with open(os.path.join(self.dir, "models", "m.inc"), "w") as fh:
            fh.write(".subckt io a b c\nR1 a b 1k\n.ends\n")
        os.makedirs(os.path.join(self.dir, "sim"))
        p = os.path.join(self.dir, "sim", "d.sp")
        with open(p, "w") as fh:
            fh.write(".include '../models/m.inc'\nX1 x y z io\nR9 x y 1k\n")
        dk = deck.read([p])
        self.assertEqual(len(dk.files), 2)
        self.assertIn("io", dk.subckts)
        self.assertEqual(dk.missing_includes, [])
        codes = [f.code for f in check.Checker(dk).run()]
        self.assertNotIn("undefined-subckt", codes)
        self.assertNotIn("port-count", codes)

    def test_missing_relative_include_is_reported(self):
        _dk, _c, f = self.lint(".include '../nope/m.inc'\nR1 a b 1k\n"
                               "R2 a b 1k\n")
        self.assertIn("missing-include", [x.code for x in f])

    def test_bare_lib_section_is_not_a_missing_file(self):
        _dk, _c, f = self.lint(".lib tt\nR1 a b 1k\nR2 a b 1k\n")
        self.assertEqual([x for x in f if x.code == "missing-include"], [])

    def test_no_includes_flag(self):
        with open(os.path.join(self.dir, "m.inc"), "w") as fh:
            fh.write(".subckt io a b\n.ends\n")
        p = self.write("d.sp", ".include 'm.inc'\nR1 a b 1k\nR2 a b 1k\n")
        dk = deck.read([p], follow_includes=False)
        self.assertEqual(len(dk.files), 1)
        self.assertEqual(dk.missing_includes, [])


class TestChecks(Harness):
    SUB = ".subckt io_cell VDD VSS PAD DIN\nR1 PAD DIN 1k\n.ends\n"

    def test_port_count_mismatch(self):
        _dk, _c, f = self.lint(
            self.SUB + "X1 vdd vss pad io_cell\nR9 pad vss 1k\n")
        self.assertIn("port-count", self.codes(f))

    def test_correct_port_count_is_quiet(self):
        _dk, _c, f = self.lint(
            self.SUB + "X1 vdd vss pad din io_cell\n"
            "R9 pad vss 1k\nR8 din vss 1k\nR7 vdd vss 1k\n")
        self.assertNotIn("port-count", self.codes(f))

    def test_undefined_subckt(self):
        _dk, _c, f = self.lint("X1 a b nosuch\nR1 a b 1k\n")
        self.assertIn("undefined-subckt", self.codes(f))

    def test_duplicate_element_name(self):
        _dk, _c, f = self.lint("R1 a b 1k\nR1 b c 1k\nR2 c a 1k\n")
        self.assertIn("duplicate-name", self.codes(f))

    def test_missing_include(self):
        _dk, _c, f = self.lint(".include 'nope.inc'\nR1 a b 1k\nR2 a b 1k\n")
        self.assertIn("missing-include", self.codes(f))

    def test_floating_net(self):
        _dk, _c, f = self.lint("R1 a b 1k\nR2 b c 1k\n")
        floating = [x.message for x in f if x.code == "floating-net"]
        self.assertTrue(any("net a " in m for m in floating))
        self.assertTrue(any("net c " in m for m in floating))

    def test_ground_and_globals_are_not_floating(self):
        _dk, _c, f = self.lint(".global vddq\nR1 a 0 1k\nR2 a vddq 1k\n")
        self.assertEqual([x for x in f if x.code == "floating-net"], [])

    def test_keep_net_exempts(self):
        _dk, _c, f = self.lint("R1 probe_x b 1k\nR2 b c 1k\n",
                               keep_nets=["^probe_", "^c$"])
        self.assertEqual([x for x in f if x.code == "floating-net"], [])

    def test_zero_ohm_resistor_reported_as_merged(self):
        _dk, c, f = self.lint("Rs a b 0\nR1 a x 1k\nR2 b y 1k\n"
                              "R3 x y 1k\n")
        merged = [x for x in f if x.code == "merged-net"]
        self.assertEqual(len(merged), 1)
        self.assertIn("a and b", merged[0].message)
        self.assertEqual(c.merged[0][2], "Rs = 0 ohm")

    def test_normal_resistor_is_not_merged(self):
        _dk, _c, f = self.lint("Rs a b 50\nR1 a x 1k\nR2 b y 1k\nR3 x y 1k\n")
        self.assertEqual([x for x in f if x.code == "merged-net"], [])

    def test_dot_connect_is_merged(self):
        _dk, _c, f = self.lint(".connect a b\nR1 a x 1k\nR2 b x 1k\n"
                               "R3 x 0 1k\n")
        self.assertIn("merged-net", self.codes(f))

    def test_isolated_instance(self):
        _dk, _c, f = self.lint(
            self.SUB + "X1 p q r s io_cell\nR1 m n 1k\nR2 n o 1k\n")
        self.assertIn("isolated-instance", self.codes(f))

    def test_case_folding_joins_nets(self):
        _dk, dk_c, f = self.lint("R1 PAD_DQ0 b 1k\nR2 pad_dq0 c 1k\n"
                                 "R3 b c 1k\n")
        self.assertEqual([x for x in f if x.code == "floating-net"], [])


class TestValueParsing(unittest.TestCase):
    def test_suffixes(self):
        self.assertEqual(check.parse_value("0"), 0.0)
        self.assertEqual(check.parse_value("1k"), 1e3)
        self.assertEqual(check.parse_value("1meg"), 1e6)
        self.assertEqual(check.parse_value("1m"), 1e-3)
        self.assertEqual(check.parse_value("1T"), 1e12)
        self.assertEqual(check.parse_value("2.5n"), 2.5e-9)
        self.assertEqual(check.parse_value("1e3"), 1e3)
        self.assertIsNone(check.parse_value("'r_expr'"))
        self.assertIsNone(check.parse_value(None))


if __name__ == "__main__":
    unittest.main()
