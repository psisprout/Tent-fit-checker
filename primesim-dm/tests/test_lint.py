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


class TestIncompleteNetlist(Harness):
    """A '.include' inside a library is often relative to the run directory,
    not to the library - and when one cannot be read, everything the checker
    concludes about connectivity is downstream of that."""

    def test_include_resolves_against_the_top_deck_directory(self):
        os.makedirs(os.path.join(self.dir, "run", "DB"))
        os.makedirs(os.path.join(self.dir, "lib"))
        with open(os.path.join(self.dir, "lib", "ch.lib"), "w") as fh:
            fh.write(".include './DB/inst.sp'\n")
        with open(os.path.join(self.dir, "run", "DB", "inst.sp"), "w") as fh:
            fh.write(".subckt rdl a b\nR1 a b 0.1\n.ends\n")
        top = os.path.join(self.dir, "run", "top.sp")
        with open(top, "w") as fh:
            fh.write(".include '../lib/ch.lib'\nX1 n1 n2 rdl\nR9 n1 n2 1k\n")
        dk = deck.read([top])
        self.assertEqual(dk.missing_includes, [])
        self.assertIn("rdl", dk.subckts)

    def test_connectivity_checks_are_skipped_when_a_file_is_missing(self):
        _dk, _c, f = self.lint(".include 'gone.inc'\n"
                               "X1 a b c io\nR1 z z2 1k\n")
        codes = [x.code for x in f]
        self.assertIn("missing-include", codes)
        self.assertIn("checks-skipped", codes)
        # the noise these would have produced is suppressed
        self.assertNotIn("floating-net", codes)
        self.assertNotIn("isolated-instance", codes)

    def test_force_connectivity_runs_them_anyway(self):
        _dk, _c, f = self.lint(".include 'gone.inc'\n"
                               "X1 a b c io\nR1 z z2 1k\n",
                               force_connectivity=True)
        codes = [x.code for x in f]
        self.assertIn("floating-net", codes)
        self.assertNotIn("checks-skipped", codes)

    def test_complete_netlist_still_runs_them(self):
        _dk, _c, f = self.lint("R1 a b 1k\nR2 b c 1k\n")
        codes = [x.code for x in f]
        self.assertIn("floating-net", codes)
        self.assertNotIn("checks-skipped", codes)


CORNERS = """\
.lib tt
.subckt ch a b
Rtt a b 0.10
.ends
Xtt_only p q ch
.endl tt
.lib ff
.subckt ch a b
Rff a b 0.08
.ends
Xff_only p q ch
.endl ff
"""


class TestLibSections(Harness):
    """`.lib file tt` activates the tt section only. Reading the whole file
    pulls every corner in at once, which shows up as the same subckt and the
    same element names defined over and over."""

    def test_only_the_named_section_is_read(self):
        self.write("corners.lib", CORNERS)
        p = self.write("top.sp", ".lib 'corners.lib' tt\n"
                                 "X1 n1 n2 ch\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        self.assertEqual(len(dk.subckts), 1)
        names = sorted(e.name for e in dk.elements)
        self.assertIn("Xtt_only", names)
        self.assertNotIn("Xff_only", names)
        self.assertEqual([f.code for f in check.Checker(dk).run()
                          if f.code == "duplicate-name"], [])

    def test_other_section_gives_the_other_content(self):
        self.write("corners.lib", CORNERS)
        p = self.write("top.sp", ".lib 'corners.lib' ff\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        self.assertIn("Xff_only", [e.name for e in dk.elements])
        self.assertNotIn("Xtt_only", [e.name for e in dk.elements])

    def test_same_file_twice_under_two_sections(self):
        self.write("corners.lib", CORNERS)
        p = self.write("top.sp", ".lib 'corners.lib' tt\n"
                                 ".lib 'corners.lib' ff\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        names = [e.name for e in dk.elements]
        self.assertIn("Xtt_only", names)
        self.assertIn("Xff_only", names)

    def test_plain_include_activates_no_section(self):
        self.write("corners.lib", CORNERS)
        p = self.write("top.sp", ".include 'corners.lib'\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        self.assertEqual(dk.subckts, {})
        self.assertEqual([e.name for e in dk.elements], ["R9"])

    def test_content_outside_any_section_is_always_read(self):
        self.write("mixed.lib", ".subckt always a b\nR1 a b 1k\n.ends\n"
                                + CORNERS)
        p = self.write("top.sp", ".lib 'mixed.lib' tt\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        self.assertIn("always", dk.subckts)
        self.assertIn("ch", dk.subckts)

    def test_line_numbers_survive_the_blanking(self):
        self.write("corners.lib", CORNERS)
        p = self.write("top.sp", ".lib 'corners.lib' ff\nR9 n1 n2 1k\n")
        dk = deck.read([p])
        xff = [e for e in dk.elements if e.name == "Xff_only"][0]
        self.assertEqual(xff.line, 11)      # its real line in corners.lib


class TestBoundaries(Harness):
    """A DM check has no business reading a transistor-level IO model or the
    PDK underneath it. Interfaces yes, insides no."""

    IO_SPF = (".subckt io_cell VDD VSS PAD DIN\n"
              "Xm1 n1 DIN VSS VSS nch w=1u\n"
              "Cp1 n1 VSS 1.2f\n.ends\n")
    TOP = ("X1 vdd vss pad din io_cell\nR9 pad vss 1k\n"
           "V2 vdd 0 DC 1.1\nV3 vss 0 DC 0\nV4 din 0 DC 0\n")

    def test_spf_is_opaque_by_default(self):
        self.write("io.spf", self.IO_SPF)
        p = self.write("top.sp", ".include 'io.spf'\n" + self.TOP)
        dk = deck.read([p])
        self.assertEqual(len(dk.opaque_files), 1)
        self.assertEqual(len(dk.files), 1)          # the spf is not listed
        self.assertIn("io_cell", dk.subckts)        # but its ports are known
        codes = [f.code for f in check.Checker(dk).run()]
        self.assertNotIn("undefined-subckt", codes)
        self.assertNotIn("port-count", codes)

    def test_opaque_still_catches_a_port_count_error(self):
        self.write("io.spf", self.IO_SPF)
        p = self.write("top.sp", ".include 'io.spf'\n"
                                 "X1 vdd vss pad io_cell\nR9 pad vss 1k\n")
        dk = deck.read([p])
        self.assertIn("port-count", [f.code for f in check.Checker(dk).run()])

    def test_opting_out_reads_the_spf(self):
        self.write("io.spf", self.IO_SPF)
        p = self.write("top.sp", ".include 'io.spf'\n" + self.TOP)
        dk = deck.read([p], opaque=[])
        self.assertEqual(dk.opaque_files, [])
        self.assertEqual(len(dk.files), 2)

    def test_skip_does_not_open_the_file(self):
        self.write("pdk.lib", ".model nch nmos level=54\n")
        self.write("io.inc", ".include 'pdk.lib'\n" + self.IO_SPF)
        p = self.write("top.sp", ".include 'io.inc'\n" + self.TOP)
        dk = deck.read([p], skip=[r"pdk\.lib$"])
        self.assertEqual(len(dk.skipped_files), 1)
        self.assertEqual(dk.missing_includes, [])   # skipped is not missing
        self.assertIn("io_cell", dk.subckts)

    def test_max_depth_stops_the_walk(self):
        self.write("deep.inc", ".subckt deep a b\nR1 a b 1k\n.ends\n")
        self.write("mid.inc", ".include 'deep.inc'\n" + self.IO_SPF)
        p = self.write("top.sp", ".include 'mid.inc'\n" + self.TOP)

        full = deck.read([p])
        self.assertIn("deep", full.subckts)

        capped = deck.read([p], max_depth=1)
        self.assertNotIn("deep", capped.subckts)
        self.assertIn("io_cell", capped.subckts)
        self.assertEqual(len(capped.depth_limited), 1)

    def test_the_top_deck_is_never_skipped_or_made_opaque(self):
        p = self.write("top.spf", self.TOP)
        dk = deck.read([p], skip=[r"\.spf$"])
        self.assertEqual(dk.skipped_files, [])
        self.assertEqual(len(dk.elements), 5)


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


class TestPortability(Harness):
    """Reads must not depend on the machine's locale, writes must not depend
    on its line-ending convention - decks get made on one box and run on
    another."""

    def test_cp949_bytes_do_not_crash_the_parser(self):
        p = os.path.join(self.dir, "m.inc")
        with open(p, "wb") as fh:
            fh.write("* \uc0c1\ud0dc \uc8fc\uc11d\n".encode("cp949"))
            fh.write(b".subckt io a b\nR1 a b 1k\n.ends\n")
        d = self.write("d.sp", ".include 'm.inc'\nX1 x y io\nR9 x y 1k\n")
        dk = deck.read([d])
        self.assertIn("io", dk.subckts)
        self.assertEqual([f.code for f in check.Checker(dk).run()
                          if f.code == "undefined-subckt"], [])

    def test_crlf_deck_parses(self):
        p = os.path.join(self.dir, "d.sp")
        with open(p, "wb") as fh:
            fh.write(b".subckt io a b\r\nR1 a b 1k\r\n.ends\r\n"
                     b"X1 x y io\r\nR9 x y 1k\r\n")
        dk = deck.read([p])
        self.assertEqual(sorted(e.name for e in dk.elements), ["R9", "X1"])
        self.assertEqual(dk.unparsed, [])


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
