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


def _resolve(raw, base_dir, search_dirs):
    raw = os.path.expandvars(os.path.expanduser(raw))
    cands = [raw] if os.path.isabs(raw) else [os.path.join(base_dir, raw)]
    if not os.path.isabs(raw):
        cands += [os.path.join(d, raw) for d in search_dirs]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def read(paths, follow_includes=True, search_dirs=(), fold_case=True):
    """Parse one or more deck files into a :class:`Deck`."""
    deck = Deck()
    queue = list(paths)
    seen = set()

    def norm(net):
        return net.lower() if fold_case else net

    while queue:
        path = os.path.abspath(queue.pop(0))
        if path in seen:
            continue
        seen.add(path)
        if not os.path.isfile(path):
            deck.missing_includes.append(path)
            continue
        with open(path, "r", errors="replace") as fh:
            text = fh.read()
        deck.files.append(path)

        for sub in spice.parse_subckts(text, path):
            deck.subckts.setdefault(sub.name.lower(), sub)

        depth = 0
        base_dir = os.path.dirname(path)
        for lineno, line in spice.logical_lines(text):
            if _DOT_SUBCKT.match(line):
                depth += 1
                continue
            if _DOT_ENDS.match(line):
                depth = max(0, depth - 1)
                continue

            m = _DOT_INCLUDE.match(line) or _DOT_LIB.match(line)
            if m:
                raw = m.group(1) or m.group(2) or m.group(3)
                if raw and not raw.startswith("."):
                    target = _resolve(raw, base_dir, search_dirs)
                    if target is None:
                        deck.missing_includes.append(
                            "%s (from %s:%d)" % (raw, os.path.basename(path),
                                                 lineno))
                    elif follow_includes:
                        queue.append(target)
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
