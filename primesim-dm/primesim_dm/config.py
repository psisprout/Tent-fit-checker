"""Config loading for the deck generator.

The config is JSON with two engineer-friendly relaxations (so it can be kept
in a repo and reviewed like source):

  * ``//`` , ``/* */`` and ``#`` comments
  * trailing commas in objects and arrays

Everything else is plain ``json``, so no third-party YAML dependency.
"""

import json
import os
import re


class ConfigError(Exception):
    pass


def strip_jsonc(text):
    """Remove comments and trailing commas from a JSONC document."""
    out = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


DEFAULTS = {
    "deck": {
        "title": "auto-generated deck",
        "output": "out.sp",
        "temp": [],
        "options": [],
        "globals": [],
        "width": 88,
    },
    "models": {
        "files": [],
        "search_dirs": [],
        "follow_includes": False,
        "expand_buses": False,
        "emit_includes": True,
    },
    "naming": {
        "case": "keep",          # keep | upper | lower
        "bus_style": "keep",     # keep | angle | bracket | paren | underscore
        "default": "same_name",  # same_name | prefix | terminate | error
        "match_normalized": True,  # match rules on the bus_style-folded name
        "prefix_sep": "_",
        "rules": [],
    },
    "termination": {
        "auto_terminate_floating": True,
        "keep_nets": [],
        "default": {"type": "rload", "to": "0", "value": "1T"},
        "overrides": [],
        "net_prefix": "n_",
    },
    "supplies": [],
    "instances": [],
    "stimulus": [],
    "analysis": {},
    "probes": [],
    "measures": [],
    "raw_prepend": [],
    "raw_append": [],
}

_VALID_TERM_TYPES = ("open", "tie", "rload", "cload", "rc", "vsource", "isource")
_DEFAULT_POLICIES = ("same_name", "prefix", "terminate", "error")
_INSTANCE_KEYS = set([
    "name", "subckt", "source", "comment", "params", "default",
    "connect", "rules", "unused", "termination",
])


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _check_rule(rule, where):
    if not isinstance(rule, dict):
        raise ConfigError("%s: each rule must be an object" % where)
    if "match" not in rule:
        raise ConfigError("%s: rule is missing 'match'" % where)
    try:
        re.compile(rule["match"])
    except re.error as exc:
        raise ConfigError("%s: bad regex %r (%s)" % (where, rule["match"], exc))
    action = rule.get("action", "connect")
    if action not in ("connect", "terminate", "skip"):
        raise ConfigError("%s: unknown action %r" % (where, action))
    if action == "connect" and "net" not in rule:
        raise ConfigError("%s: connect rule needs 'net'" % where)


def _check_term(term, where):
    if not isinstance(term, dict):
        raise ConfigError("%s: termination must be an object" % where)
    t = term.get("type", "rload")
    if t not in _VALID_TERM_TYPES:
        raise ConfigError("%s: unknown termination type %r (expected one of %s)"
                          % (where, t, ", ".join(_VALID_TERM_TYPES)))


def validate(cfg):
    if cfg["naming"]["default"] not in _DEFAULT_POLICIES:
        raise ConfigError("naming.default must be one of %s"
                          % "/".join(_DEFAULT_POLICIES))
    if cfg["naming"]["case"] not in ("keep", "upper", "lower"):
        raise ConfigError("naming.case must be keep/upper/lower")
    for i, r in enumerate(cfg["naming"]["rules"]):
        _check_rule(r, "naming.rules[%d]" % i)
    _check_term(cfg["termination"]["default"], "termination.default")
    for i, r in enumerate(cfg["termination"]["overrides"]):
        _check_rule({"match": r.get("match", ""), "net": "x"},
                    "termination.overrides[%d]" % i)
        _check_term(r, "termination.overrides[%d]" % i)
    names = set()
    if not cfg["instances"]:
        raise ConfigError("config has no 'instances' - nothing to generate")
    for i, inst in enumerate(cfg["instances"]):
        where = "instances[%d]" % i
        if not isinstance(inst, dict):
            raise ConfigError("%s: must be an object" % where)
        for key in ("name", "subckt"):
            if not inst.get(key):
                raise ConfigError("%s: missing '%s'" % (where, key))
        if inst["name"] in names:
            raise ConfigError("%s: duplicate instance name %r"
                              % (where, inst["name"]))
        names.add(inst["name"])
        stray = set(inst) - _INSTANCE_KEYS
        if stray:
            raise ConfigError("%s: unknown key(s): %s (known: %s)"
                              % (where, ", ".join(sorted(stray)),
                                 ", ".join(sorted(_INSTANCE_KEYS))))
        if inst.get("default") and inst["default"] not in _DEFAULT_POLICIES:
            raise ConfigError("%s: default must be one of %s"
                              % (where, "/".join(_DEFAULT_POLICIES)))
        for j, r in enumerate(inst.get("rules", []) or []):
            _check_rule(r, "%s.rules[%d]" % (where, j))
        if inst.get("termination"):
            _check_term(inst["termination"], "%s.termination" % where)
    return cfg


def normalize(raw, base_dir="."):
    """Apply defaults, resolve relative paths, validate."""
    if not isinstance(raw, dict):
        raise ConfigError("top level of the config must be an object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ConfigError("unknown top-level key(s): %s (known: %s)"
                          % (", ".join(sorted(unknown)),
                             ", ".join(sorted(DEFAULTS))))
    cfg = _merge(DEFAULTS, raw)
    cfg["_base_dir"] = base_dir

    def abspath(p):
        p = os.path.expandvars(os.path.expanduser(p))
        return p if os.path.isabs(p) else os.path.normpath(
            os.path.join(base_dir, p))

    files = []
    for entry in cfg["models"]["files"]:
        if isinstance(entry, str):
            files.append({"path": abspath(entry)})
        elif isinstance(entry, dict):
            if "path" not in entry:
                raise ConfigError("models.files entry needs a 'path'")
            e = dict(entry)
            e["path"] = abspath(entry["path"])
            files.append(e)
        else:
            raise ConfigError("models.files entries must be a string or object")
    cfg["models"]["files"] = files
    cfg["models"]["search_dirs"] = [abspath(d)
                                    for d in cfg["models"]["search_dirs"]]
    if isinstance(cfg["deck"]["temp"], (int, float, str)):
        cfg["deck"]["temp"] = [cfg["deck"]["temp"]]
    return validate(cfg)


def _read_raw(path):
    with open(path, "r") as fh:
        text = fh.read()
    try:
        raw = json.loads(strip_jsonc(text))
    except ValueError as exc:
        raise ConfigError("%s: invalid JSON/JSONC: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("%s: top level must be an object" % path)
    return raw


def _absolutize_paths(raw, base_dir):
    """Make model paths in one config file absolute w.r.t. that file."""
    def ab(p):
        p = os.path.expandvars(os.path.expanduser(str(p)))
        return p if os.path.isabs(p) else os.path.normpath(
            os.path.join(base_dir, p))

    models = raw.get("models")
    if not isinstance(models, dict):
        return raw
    files = models.get("files")
    if isinstance(files, list):
        models["files"] = [
            ab(f) if isinstance(f, str)
            else (dict(f, path=ab(f["path"])) if isinstance(f, dict) and "path" in f
                  else f)
            for f in files]
    dirs = models.get("search_dirs")
    if isinstance(dirs, list):
        models["search_dirs"] = [ab(d) for d in dirs]
    return raw


def load(path):
    """Load a config, applying any ``extends`` chain.

    Merging is deep for objects and replacing for lists, so a child config can
    override ``naming.default`` without restating ``naming.rules`` - but a
    child that defines ``naming.rules`` replaces the parent's list outright.
    """
    path = os.path.abspath(path)
    return normalize(load_raw_chain(path), base_dir=os.path.dirname(path))


def load_raw_chain(path, _seen=None):
    """Merge a config with everything it ``extends``, before defaults."""
    path = os.path.abspath(path)
    _seen = list(_seen or [])
    if path in _seen:
        raise ConfigError("circular 'extends': %s"
                          % " -> ".join(_seen + [path]))
    _seen.append(path)
    base_dir = os.path.dirname(path)
    raw = _absolutize_paths(_read_raw(path), base_dir)
    parents = raw.pop("extends", None)
    if not parents:
        return raw
    if isinstance(parents, str):
        parents = [parents]
    merged = {}
    for p in parents:
        p = os.path.expandvars(os.path.expanduser(p))
        if not os.path.isabs(p):
            p = os.path.join(base_dir, p)
        if not os.path.isfile(p):
            raise ConfigError("%s: extends target not found: %s" % (path, p))
        merged = _merge(merged, load_raw_chain(p, _seen))
    return _merge(merged, raw)
