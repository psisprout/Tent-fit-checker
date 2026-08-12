"""SPICE / PrimeSim netlist text utilities.

Deliberately stdlib-only and conservative about syntax so it can run on the
old python3 that ships inside most EDA environments (3.6+).

Handles the parts of the deck syntax that matter for port discovery:
  * full-line comments (``*``) and inline ``$`` comments
  * ``+`` line continuation
  * ``.subckt`` / ``.ends`` (incl. nesting, ``param:``, parenthesised ports)
  * ``.include`` / ``.lib`` so a top model file can pull in the rest
"""

import os
import re

_FULL_COMMENT = re.compile(r"^\s*\*")
_CONT = re.compile(r"^\s*\+")
_SUBCKT = re.compile(r"^\s*\.subckt\b", re.I)
_ENDS = re.compile(r"^\s*\.ends\b", re.I)
_INCLUDE = re.compile(
    r"^\s*\.inc(?:lude)?\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))", re.I)
_LIB = re.compile(
    r"^\s*\.lib\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))(?:\s+(\S+))?", re.I)

# port-name tables that S-parameter exports put in header comments
_NODE_ANNOT = [
    re.compile(r"^\s*\*\s*node\s*(\d+)[\s:=]+(\S+)", re.I),
    re.compile(r"^\s*\*\s*port\s*(\d+)[\s:=]+(\S+)", re.I),
]

# ``DQ[7:0]`` / ``DQ<7:0>`` / ``DQ(7:0)`` style bus declarations in a port list
_BUS = re.compile(r"^(?P<base>.+?)[\[<\(](?P<hi>\d+)\s*:\s*(?P<lo>\d+)[\]>\)]$")

BUS_STYLES = {
    "angle": "{base}<{idx}>",
    "bracket": "{base}[{idx}]",
    "paren": "{base}({idx})",
    "underscore": "{base}_{idx}",
}

_BUS_ANY = re.compile(r"^(?P<base>.+?)[\[<\(](?P<idx>\d+)[\]>\)]$")


class SpiceError(Exception):
    pass


class Subckt(object):
    """One ``.subckt`` definition."""

    def __init__(self, name, ports, params, path, line, depth):
        self.name = name
        self.ports = ports            # ordered list of port names
        self.params = params          # dict of default parameters
        self.path = path              # file it was found in
        self.line = line              # 1-based line number of the .subckt
        self.end_line = line          # line of the matching .ends
        self.depth = depth            # 0 = top level, >0 = nested
        self.node_names = {}          # 1-based port index -> name from *node

    def __repr__(self):
        return "<Subckt %s (%d ports) %s:%d>" % (
            self.name, len(self.ports), self.path, self.line)

    def label(self, index):
        """Best available name for port ``index`` (0-based).

        S-parameter channel models declare ports as bare numbers and carry the
        real names in ``*node`` comments; everywhere else the port token is
        already the name.
        """
        return self.node_names.get(index + 1, self.ports[index])

    def labels(self):
        return [self.label(i) for i in range(len(self.ports))]

    def annotation_problems(self):
        """Complaints about the ``*node`` table, if there is one."""
        if not self.node_names:
            return []
        out = []
        n = len(self.ports)
        missing = [i for i in range(1, n + 1) if i not in self.node_names]
        extra = sorted(i for i in self.node_names if i < 1 or i > n)
        if missing:
            out.append("%s: no *node comment for port index %s"
                       % (self.name, _ranges(missing)))
        if extra:
            out.append("%s: *node comment for index %s but the subckt only "
                       "has %d ports" % (self.name, _ranges(extra), n))
        seen = {}
        for idx, nm in sorted(self.node_names.items()):
            if nm in seen:
                out.append("%s: *node name %r used for both port %d and %d"
                           % (self.name, nm, seen[nm], idx))
            seen[nm] = idx
        return out

    def to_dict(self):
        return {
            "name": self.name,
            "ports": list(self.ports),
            "labels": self.labels(),
            "node_names": dict((str(k), v)
                               for k, v in sorted(self.node_names.items())),
            "params": dict(self.params),
            "path": self.path,
            "line": self.line,
            "depth": self.depth,
        }


def _ranges(nums):
    """[1,2,3,7] -> '1-3, 7'"""
    nums = sorted(nums)
    out = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out)


def strip_comments(line):
    """Return the code part of a physical line ('' for a pure comment)."""
    if _FULL_COMMENT.match(line):
        return ""
    # HSPICE / PrimeSim inline comment character
    idx = line.find("$")
    if idx >= 0:
        line = line[:idx]
    return line.rstrip()


def logical_lines(text):
    """Yield ``(lineno, joined_text)``, folding ``+`` continuations.

    ``lineno`` is the 1-based line number of the *first* physical line.
    """
    buf = []
    start = 0
    for n, raw in enumerate(text.splitlines(), 1):
        code = strip_comments(raw)
        if not code.strip():
            continue
        if _CONT.match(code):
            if not buf:
                # stray continuation - keep it rather than crashing
                buf = [code.lstrip()[1:]]
                start = n
            else:
                buf.append(code.lstrip()[1:])
            continue
        if buf:
            yield start, " ".join(buf)
        buf = [code]
        start = n
    if buf:
        yield start, " ".join(buf)


def _tokenize(text):
    """Split a logical line into tokens, keeping ``k=v`` pairs together."""
    text = text.replace("(", " ( ").replace(")", " ) ")
    toks = [t for t in text.split() if t not in ("(", ")")]
    # re-join tokens split around '=' e.g. "w = 1u" -> "w=1u"
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "=" and out:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            out[-1] = out[-1] + "=" + nxt
            i += 2
            continue
        if t.startswith("=") and out:
            # "type =slow" - space only on the left of the '='
            out[-1] = out[-1] + t
            i += 1
            continue
        if t.endswith("=") and i + 1 < len(toks):
            out.append(t + toks[i + 1])
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def expand_bus(port):
    """``DQ[7:0]`` -> ``['DQ[7]', ... 'DQ[0]']``; plain names pass through."""
    m = _BUS.match(port)
    if not m:
        return [port]
    base = m.group("base")
    hi = int(m.group("hi"))
    lo = int(m.group("lo"))
    open_c = port[len(base)]
    close_c = port[-1]
    step = -1 if hi >= lo else 1
    return ["%s%s%d%s" % (base, open_c, i, close_c)
            for i in range(hi, lo + step, step)]


def normalize_bus(name, style):
    """Rewrite the bus delimiter of a single net/port name.

    ``style`` is one of ``angle``/``bracket``/``paren``/``underscore``/``keep``.
    """
    if not style or style == "keep":
        return name
    fmt = BUS_STYLES.get(style)
    if fmt is None:
        raise SpiceError("unknown bus_style: %s" % style)
    m = _BUS_ANY.match(name)
    if not m:
        return name
    return fmt.format(base=m.group("base"), idx=int(m.group("idx")))


def parse_node_annotations(text):
    """Collect ``*node <n> <name>`` header comments.

    S-parameter channel models (mem package, board, RDL ...) declare their
    ports as bare numbers::

        *node   1    CA0_A_BGA1_G4_T1
        *node   2    CA0_A_DIE1_46_T1
        .subckt mempkg_sp 1 2 3 ... n

    so the port list carries no meaning and this comment table is the only
    thing that says which physical node each port is.  Returns a list of
    ``(lineno, index, name)``.
    """
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.lstrip().startswith("*"):
            continue
        for rx in _NODE_ANNOT:
            m = rx.match(raw)
            if m:
                out.append((n, int(m.group(1)), m.group(2)))
                break
    return out


def _attach_annotations(found, annots):
    """Give each annotation to the subckt it belongs to.

    The table sits immediately above its ``.subckt`` (sometimes inside the
    body), so an annotation belongs to the first subckt whose body ends at or
    after it, as long as no earlier subckt already closed past that point.
    """
    if not annots or not found:
        return
    tops = [s for s in found if s.depth == 0] or found
    tops = sorted(tops, key=lambda s: s.line)
    for lineno, idx, name in annots:
        owner = None
        prev_end = 0
        for s in tops:
            if prev_end < lineno <= s.end_line:
                owner = s
                break
            prev_end = s.end_line
        if owner is not None:
            owner.node_names[idx] = name


def parse_subckts(text, path="<string>", expand_buses=False):
    """Return every ``.subckt`` found in ``text``."""
    found = []
    stack = []
    for lineno, line in logical_lines(text):
        if _SUBCKT.match(line):
            toks = _tokenize(line)[1:]          # drop '.subckt'
            if not toks:
                raise SpiceError("%s:%d: .subckt without a name" % (path, lineno))
            name = toks[0]
            ports = []
            params = {}
            in_params = False
            for tok in toks[1:]:
                low = tok.lower()
                if low in ("param:", "params:", "param", "params"):
                    in_params = True
                    continue
                if "=" in tok:
                    in_params = True
                    k, _, v = tok.partition("=")
                    params[k] = v
                    continue
                if in_params:
                    continue
                if expand_buses:
                    ports.extend(expand_bus(tok))
                else:
                    ports.append(tok)
            sub = Subckt(name, ports, params, path, lineno, len(stack))
            found.append(sub)
            stack.append(sub)
        elif _ENDS.match(line):
            if stack:
                stack.pop().end_line = lineno
    for sub in stack:                      # unterminated .subckt
        sub.end_line = max(sub.end_line, lineno if found else sub.line)
    _attach_annotations(found, parse_node_annotations(text))
    return found


def _resolve_path(raw, base_dir, search_dirs):
    raw = os.path.expandvars(os.path.expanduser(raw))
    cands = [raw] if os.path.isabs(raw) else []
    if not os.path.isabs(raw):
        cands.append(os.path.join(base_dir, raw))
        for d in search_dirs:
            cands.append(os.path.join(d, raw))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def scan_files(paths, follow_includes=False, search_dirs=(),
               expand_buses=False, warn=None):
    """Parse every file in ``paths`` (optionally following .include/.lib).

    Returns ``(subckts, files_read)``.  ``warn`` is an optional callable that
    receives a message string for each problem (missing include, etc).
    """
    warn = warn or (lambda msg: None)
    seen = []
    seen_set = set()
    out = []
    queue = list(paths)
    while queue:
        p = queue.pop(0)
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(p)))
        if p in seen_set:
            continue
        if not os.path.isfile(p):
            warn("model file not found: %s" % p)
            continue
        seen_set.add(p)
        seen.append(p)
        try:
            with open(p, "r", errors="replace") as fh:
                text = fh.read()
        except IOError as exc:
            warn("cannot read %s: %s" % (p, exc))
            continue
        out.extend(parse_subckts(text, p, expand_buses=expand_buses))
        if not follow_includes:
            continue
        base_dir = os.path.dirname(p)
        for _lineno, line in logical_lines(text):
            m = _INCLUDE.match(line) or _LIB.match(line)
            if not m:
                continue
            raw = m.group(1) or m.group(2) or m.group(3)
            if not raw or raw.startswith("."):
                continue
            resolved = _resolve_path(raw, base_dir, search_dirs)
            if resolved:
                queue.append(resolved)
            else:
                warn("could not resolve include %r from %s" % (raw, p))
    return out, seen


def wrap(tokens, width=88, cont="+ ", indent=""):
    """Render tokens as a deck line, folding with ``+`` continuations."""
    lines = []
    cur = indent
    for tok in tokens:
        piece = tok if not cur.strip() else " " + tok
        if cur.strip() and len(cur) + len(piece) > width:
            lines.append(cur)
            cur = cont + tok
        else:
            cur += piece
    if cur.strip():
        lines.append(cur)
    return lines
