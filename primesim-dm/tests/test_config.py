import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from primesim_dm import config


class TestJsonc(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        src = """
        {
          // line comment
          "a": 1,   # hash comment
          /* block
             comment */
          "b": [1, 2, 3,],
          "c": "keep // this",
        }
        """
        import json
        got = json.loads(config.strip_jsonc(src))
        self.assertEqual(got, {"a": 1, "b": [1, 2, 3], "c": "keep // this"})


class TestValidate(unittest.TestCase):
    def base(self, **over):
        raw = {"instances": [{"name": "X1", "subckt": "cell"}]}
        raw.update(over)
        return raw

    def test_defaults_applied(self):
        cfg = config.normalize(self.base())
        self.assertEqual(cfg["naming"]["default"], "same_name")
        self.assertEqual(cfg["termination"]["default"]["type"], "rload")

    def test_unknown_top_level_key(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          self.base(naming_rules=[]))

    def test_bad_regex(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          self.base(naming={"rules": [{"match": "([",
                                                       "net": "x"}]}))

    def test_bad_termination_type(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          self.base(termination={"default": {"type": "magic"}}))

    def test_duplicate_instance_name(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          {"instances": [{"name": "X1", "subckt": "a"},
                                         {"name": "X1", "subckt": "b"}]})

    def test_no_instances(self):
        self.assertRaises(config.ConfigError, config.normalize, {})

    def test_unknown_instance_key(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          {"instances": [{"name": "X1", "subckt": "cell",
                                          "conect": {}}]})

    def test_bad_instance_default_policy(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          {"instances": [{"name": "X1", "subckt": "cell",
                                          "default": "same-name"}]})

    def test_connect_rule_needs_net(self):
        self.assertRaises(config.ConfigError, config.normalize,
                          self.base(naming={"rules": [{"match": "^A"}]}))


class TestExtends(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_child_overrides_parent(self):
        self.write("base.jsonc", """
        { "naming": { "case": "lower", "default": "same_name",
                      "rules": [{"match": "^A", "net": "a"}] },
          "deck": { "title": "base", "width": 70 } }
        """)
        child = self.write("child.jsonc", """
        { "extends": "base.jsonc",
          "deck": { "title": "child" },
          "instances": [{"name": "X1", "subckt": "cell"}] }
        """)
        cfg = config.load(child)
        self.assertEqual(cfg["deck"]["title"], "child")
        self.assertEqual(cfg["deck"]["width"], 70)      # inherited
        self.assertEqual(cfg["naming"]["case"], "lower")
        self.assertEqual(len(cfg["naming"]["rules"]), 1)

    def test_child_list_replaces_parent_list(self):
        self.write("base.jsonc",
                   '{"naming": {"rules": [{"match": "^A", "net": "a"},'
                   '{"match": "^B", "net": "b"}]}}')
        child = self.write("child.jsonc",
                           '{"extends": "base.jsonc",'
                           ' "naming": {"rules": [{"match": "^C", "net": "c"}]},'
                           ' "instances": [{"name": "X1", "subckt": "cell"}]}')
        cfg = config.load(child)
        self.assertEqual([r["match"] for r in cfg["naming"]["rules"]], ["^C"])

    def test_parent_model_paths_resolve_against_the_parent(self):
        os.makedirs(os.path.join(self.dir, "sub"))
        self.write("models.inc", ".subckt cell a\n.ends\n")
        self.write("base.jsonc", '{"models": {"files": ["models.inc"]}}')
        child = os.path.join(self.dir, "sub", "child.jsonc")
        with open(child, "w") as fh:
            fh.write('{"extends": "../base.jsonc",'
                     ' "instances": [{"name": "X1", "subckt": "cell"}]}')
        cfg = config.load(child)
        self.assertEqual(cfg["models"]["files"][0]["path"],
                         os.path.join(self.dir, "models.inc"))

    def test_circular_extends(self):
        self.write("a.jsonc", '{"extends": "b.jsonc"}')
        self.write("b.jsonc", '{"extends": "a.jsonc"}')
        self.assertRaises(config.ConfigError, config.load,
                          os.path.join(self.dir, "a.jsonc"))

    def test_missing_extends_target(self):
        p = self.write("a.jsonc", '{"extends": "nope.jsonc"}')
        self.assertRaises(config.ConfigError, config.load, p)


class TestExamplesEndToEnd(unittest.TestCase):
    """The shipped examples must generate cleanly - they are the smoke test."""

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "primesim_dm"] + list(args),
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)

    def test_examples_generate_without_warnings(self):
        out_dir = tempfile.mkdtemp()
        try:
            for name in ("hbm_tx_rx", "lpddr_dq"):
                cfg_path = os.path.join("examples", "%s.jsonc" % name)
                deck = os.path.join(out_dir, "%s.sp" % name)
                res = self.run_cli("gen", cfg_path, "-o", deck, "--strict")
                self.assertEqual(res.returncode, 0,
                                 "%s failed:\n%s" % (name, res.stderr))
                with open(deck) as fh:
                    text = fh.read()
                self.assertIn(".end", text)
                self.assertTrue(os.path.isfile(deck + ".report.txt"))
        finally:
            shutil.rmtree(out_dir)

    def test_scan_json(self):
        res = self.run_cli("scan", "examples/models/hbm_io.inc", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        import json
        subs = json.loads(res.stdout)
        names = [s["name"] for s in subs]
        self.assertIn("hbm_tx_drv", names)

    def test_init_scaffold_then_gen(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(tmp, "deck.jsonc")
            res = self.run_cli("init", "examples/models/hbm_io.inc",
                               "--subckt", "hbm_vref_gen",
                               "-o", cfg_path,
                               "--deck", os.path.join(tmp, "deck.sp"))
            self.assertEqual(res.returncode, 0, res.stderr)
            res = self.run_cli("gen", cfg_path)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "deck.sp")))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
