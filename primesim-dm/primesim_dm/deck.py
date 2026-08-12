"""Read a flat SPICE/PrimeSim deck into a connectivity graph.

This is what lets the checker run on decks nobody generated - the ones
already sitting in the project, written by hand.

Parsing a netlist for *connectivity* only needs one hard question answered
per line: which tokens are nodes?  That differs by element type, and no
parser gets every HSPICE element right.  So every line this module cannot
confidently split is recorded in :attr:`Deck.unparsed` rather than guessed
at - a check result is only trustworthy if it comes with the list of lines
it could not read.
"""

import os
import re

from . import spice

# how many nodes each element type takes, when it is a fixed number
_FIXED_NODES = {
    "R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "D": 2,
    "E": 4, "G": 4, "F": 2, "H": 2, "T": 4,
}
# these end their node list at the first key=value token
_KV_TERMINATED = ("S", "B", "P")
# these end with a model name: nodes are every non-parameter token but the last
_MODEL_TERMINATED = ("X", "Q", "M", "J", "Z")
_NO_NODES = ("K",)

# Files whose insides never matter for a DM connectivity check: parasitic
# extractions are enormous and describe the inside of a cell, not how the
# link is wired.  Their .subckt interfaces are still read.
DEFAULT_OPAQUE = [r"\.spf$", r"\.dspf$", r"\.spef$", r"\.rcx$"]

_DOT_CONNECT = re.compile(r"^\s*\.connect\s+(\S+)\s+(\S+)", re.I)
_DOT_GLOBAL = re.compile(r"^\s*\.global\s+(.*)", re.I)
_DOT_SUBCKT = re.compile(r"^\s*\.subckt\b", re.I)
_DOT_ENDS = re.compile(r"^\s*\.ends\b", re.I)
_DOT_INCLUDE = re.compile(
    r"^\s*\.inc(?:lude)?\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))", re.I)
_DOT_LIB = re.compile(
    r"^\s*\.lib\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))(?:\s+(\S+))?", re.I)


def is_kv(token):
    return "=" in token


class Element(object):
    """One instantiated device at the top level of a deck."""

    def __init__(self, name, kind, nodes, tail, path, line):
        self.name = name
        self.kind = kind          # upper-case first letter
        self.nodes = nodes        # ordered node names
        self.tail = tail          # everything after the nodes
        self.path = path
        self.line = line

    @property
    def subckt(self):
        """For an X instance, the subckt being called."""
        if self.kind != "X":
            return None
        rest = [t for t in self.tail if not is_kv(t)]
        return rest[0] if rest else None

    def where(self):
        return "%s:%d" % (os.path.basename(self.path), self.line)

    def __repr__(self):
        return "<%s %s %d nodes>" % (self.kind, self.name, len(self.nodes))


class Deck(object):
    def __init__(self):
        self.elements = []
        self.subckts = {}          # lower name -> Subckt (definitions seen)
        self.globals = set()
        self.connects = []         # (a, b, where) from .connect
        self.unparsed = []         # (path, line, text, reason)
        self.files = []
        self.missing_includes = []
        self.searched_dirs = []
        self.opaque_files = []     # read for .subckt interfaces only
        self.skipped_files = []    # not read at all
        self.depth_limited = []    # includes below these were not followed
        self.net_users = {}        # net -> [(element, port_index)]

    def index_nets(self):
        self.net_users = {}
        for el in self.elements:
            for i, n in enumerate(el.nodes):
                self.net_users.setdefault(n, []).append((el, i))
        return self.net_users

    def nets(self):
        return sorted(self.net_users)


def _split_nodes(tokens, kind, deck_subckts):
    """Return ``(nodes, tail)`` or raise ValueError if it cannot be decided."""
    if kind in _NO_NODES:
        return [], tokens

    if kind in _MODEL_TERMINATED:
        # Xname n1 .. nN subcktname [k=v ...]   /   M1 nd ng ns nb model w=..
        head = []
        for t in tokens:
            if is_kv(t):
                break
            head.append(t)
        if len(head) < 2:
            raise ValueError("needs at least one node and a model/subckt name")
        # the last non-parameter token is the model or subckt being called
        return head[:-1], tokens[len(head) - 1:]

    if kind == "W":
        n = None
        for t in tokens:
            if is_kv(t) and t.split("=", 1)[0].lower() == "n":
                try:
                    n = int(t.split("=", 1)[1])
                except ValueError:
                    raise ValueError("W element with non-numeric N=")
                break
        if n is None:
            raise ValueError("W element without N=")
        count = 2 * (n + 1)
        if len(tokens) < count:
            raise ValueError("W element declares N=%d but has %d tokens"
                             % (n, len(tokens)))
        return tokens[:count], tokens[count:]

    if kind in _KV_TERMINATED:
        nodes = []
        for t in tokens:
            if is_kv(t):
                break
            nodes.append(t)
        if not nodes:
            raise ValueError("no nodes before the first key=value")
        return nodes, tokens[len(nodes):]

    count = _FIXED_NODES.get(kind)
    if count is None:
        raise ValueError("unhandled element type %r" % kind)
    if len(tokens) < count:
        raise ValueError("expected %d nodes, line has %d tokens"
                         % (count, len(tokens)))
    return tokens[:count], tokens[count:]


def resolve_dirs(base_dir, deck_dirs, search_dirs):
    """Where to look for a relative include, most specific first.

    A ``.include './DB/x.sp'`` inside a library is not necessarily relative
    to that library: SPICE resolves it against the directory the simulator
    was launched from, which in practice is where the top deck lives.  So
    the including file's directory is tried first, then the top deck's, then
    the current directory, then anything --search-dir added.
    """
    out = []
    for d in [base_dir] + list(deck_dirs) + [os.getcwd()] + list(search_dirs):
        d = os.path.abspath(d)
        if d not in out:
            out.append(d)
    return out


def _resolve(raw, dirs):
    raw = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isabs(raw):
        return raw if os.path.isfile(raw) else None
    for d in dirs:
        cand = os.path.join(d, raw)
        if os.path.isfile(cand):
            return cand
    return None


def read(paths, follow_includes=True, search_dirs=(), fold_case=True,
         opaque=None, skip=(), max_depth=None):
    """Parse one or more deck files into a :class:`Deck`.

    ``opaque`` and ``skip`` are path patterns that draw a boundary around the
    parts of the tree a DM check has no business reading - a transistor-level
    IO model, a PDK, an extracted parasitic file.  An opaque file still gives
    up its ``.subckt`` interfaces, so port counts stay checkable; a skipped
    one is not opened at all.  ``max_depth`` stops the walk after N levels of
    include.
    """
    opaque_rx = [re.compile(p, re.I)
                 for p in (DEFAULT_OPAQUE if opaque is None else opaque)]
    skip_rx = [re.compile(p, re.I) for p in skip]

    deck = Deck()
    # a file can legitimately be read twice under two different .lib
    # sections, so the queue and the seen-set are keyed on both
    queue = [(p, None, 0) for p in paths]
    seen = set()
    deck_dirs = [os.path.dirname(os.path.abspath(p)) for p in paths]
    deck.searched_dirs = []

    def norm(net):
        return net.lower() if fold_case else net

    while queue:
        path, section, depth = queue.pop(0)
        path = os.path.abspath(path)
        if (path, section) in seen:
            continue
        seen.add((path, section))
        if depth and any(rx.search(path) for rx in skip_rx):
            deck.skipped_files.append(path)
            continue
        if not os.path.isfile(path):
            deck.missing_includes.append(path)
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if section or spice.lib_sections(text):
            # only the selected .lib section is live; without one, the
            # sections in the file are definitions nobody activated
            text = spice.select_lib_section(text, section)

        if depth and any(rx.search(path) for rx in opaque_rx):
            # interface only: the ports are what the deck connects to
            for sub in spice.parse_subckts(text, path):
                deck.subckts.setdefault(sub.name.lower(), sub)
            deck.opaque_files.append(path)
            continue

        deck.files.append(path + (" [.lib %s]" % section if section else ""))

        for sub in spice.parse_subckts(text, path):
            deck.subckts.setdefault(sub.name.lower(), sub)

        at_limit = max_depth is not None and depth >= max_depth
        if at_limit and path not in deck.depth_limited:
            deck.depth_limited.append(path)

        depth = 0
        base_dir = os.path.dirname(path)
        for lineno, line in spice.logical_lines(text):
            if _DOT_SUBCKT.match(line):
                depth += 1
                continue
            if _DOT_ENDS.match(line):
                depth = max(0, depth - 1)
                continue

            if _DOT_INCLUDE.match(line) or _DOT_LIB.match(line):
                found = spice.include_target(line)
                if found:
                    raw, sub_section = found
                    dirs = resolve_dirs(base_dir, deck_dirs, search_dirs)
                    for d in dirs:
                        if d not in deck.searched_dirs:
                            deck.searched_dirs.append(d)
                    target = _resolve(raw, dirs)
                    if target is None:
                        deck.missing_includes.append(
                            "%s (from %s:%d)" % (raw, os.path.basename(path),
                                                 lineno))
                    elif follow_includes and not at_limit:
                        queue.append((target, sub_section, depth + 1))
                continue

            if depth:                      # only the top level is checked
                continue

            m = _DOT_GLOBAL.match(line)
            if m:
                deck.globals.update(norm(t) for t in m.group(1).split())
                continue
            m = _DOT_CONNECT.match(line)
            if m:
                deck.connects.append((norm(m.group(1)), norm(m.group(2)),
                                      "%s:%d" % (path, lineno)))
                continue
            if line.lstrip().startswith("."):
                continue                   # any other directive

            toks = spice._tokenize(line)
            if not toks:
                continue
            name = toks[0]
            kind = name[0].upper()
            try:
                nodes, tail = _split_nodes(toks[1:], kind, deck.subckts)
            except ValueError as exc:
                deck.unparsed.append((path, lineno, line, str(exc)))
                continue
            deck.elements.append(
                Element(name, kind, [norm(n) for n in nodes], tail,
                        path, lineno))

    deck.index_nets()
    return deck
