"""Structural checks over a parsed deck.

Everything here works with no configuration at all, so it can be pointed at
a deck that already exists.  Nothing in this module knows what a DQ is -
that is the semantic layer's job.  These are the mistakes that are wrong
regardless of what the deck is meant to represent.
"""

import os
import re

SEV_ERROR = "error"
SEV_WARN = "warn"
SEV_INFO = "info"

_ORDER = {SEV_ERROR: 0, SEV_WARN: 1, SEV_INFO: 2}

_SUFFIX = [
    ("meg", 1e6), ("mil", 25.4e-6),
    ("t", 1e12), ("g", 1e9), ("k", 1e3), ("x", 1e6),
    ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12),
    ("f", 1e-15), ("a", 1e-18),
]
_NUM = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)$")


def parse_value(token):
    """SPICE number with an engineering suffix -> float, or None."""
    if token is None:
        return None
    token = token.strip().strip("'\"")
    m = _NUM.match(token)
    if not m:
        return None
    base = float(m.group(1))
    suffix = m.group(2).lower()
    if not suffix:
        return base
    for name, mult in _SUFFIX:          # 'meg' before 'm'
        if suffix.startswith(name):
            return base * mult
    return base                          # unknown trailing unit e.g. "10ohm"


class Finding(object):
    def __init__(self, severity, code, message, where=""):
        self.severity = severity
        self.code = code
        self.message = message
        self.where = where

    def __repr__(self):
        return "<%s %s %s>" % (self.severity, self.code, self.message)

    def line(self):
        loc = ("  [%s]" % self.where) if self.where else ""
        return "%-5s %-18s %s%s" % (self.severity.upper(), self.code,
                                    self.message, loc)


GROUND_NAMES = ("0", "gnd", "gnd!", "vss", "0.0")


class Checker(object):
    def __init__(self, deck, short_ohms=1e-6, ground_names=None,
                 keep_nets=(), force_connectivity=False):
        self.deck = deck
        self.force_connectivity = force_connectivity
        self.short_ohms = short_ohms
        self.ground = set(n.lower() for n in (ground_names or ("0", "gnd")))
        self.keep = [re.compile(p) for p in keep_nets]
        self.findings = []
        self.merged = []       # (net_a, net_b, why, where) - electrically one

    def add(self, sev, code, msg, where=""):
        self.findings.append(Finding(sev, code, msg, where))

    # -- individual checks ----------------------------------------------
    @property
    def netlist_incomplete(self):
        """True when part of the netlist was never read."""
        return bool(self.deck.missing_includes)

    def check_includes(self):
        for miss in self.deck.missing_includes:
            self.add(SEV_ERROR, "missing-include",
                     "cannot find included file: %s" % miss)
        if self.netlist_incomplete:
            self.add(SEV_ERROR, "missing-include",
                     "%d include(s) could not be read, so part of the netlist "
                     "is missing. Directories tried: %s. Add --search-dir for "
                     "the rest."
                     % (len(self.deck.missing_includes),
                        ", ".join(self.deck.searched_dirs[:4])
                        or "(none)"))

    def check_unparsed(self):
        if not self.deck.unparsed:
            return
        self.add(SEV_WARN, "unparsed-line",
                 "%d line(s) could not be split into nodes; connectivity "
                 "below does not account for them"
                 % len(self.deck.unparsed))
        for path, line, text, reason in self.deck.unparsed[:20]:
            self.add(SEV_INFO, "unparsed-line", "%s (%s)"
                     % (text[:90], reason),
                     "%s:%d" % (os.path.basename(path), line))

    def check_duplicate_names(self):
        seen = {}
        for el in self.deck.elements:
            key = el.name.lower()
            if key in seen:
                self.add(SEV_ERROR, "duplicate-name",
                         "element %s is defined twice (first at %s)"
                         % (el.name, seen[key].where()), el.where())
            else:
                seen[key] = el

    def check_instances(self):
        for el in self.deck.elements:
            if el.kind != "X":
                continue
            name = el.subckt
            if not name:
                self.add(SEV_ERROR, "no-subckt",
                         "%s has no subckt name" % el.name, el.where())
                continue
            sub = self.deck.subckts.get(name.lower())
            if sub is None:
                self.add(SEV_ERROR, "undefined-subckt",
                         "%s calls subckt %r, which is not defined in any "
                         "file read" % (el.name, name), el.where())
                continue
            if len(el.nodes) != len(sub.ports):
                self.add(SEV_ERROR, "port-count",
                         "%s passes %d node(s) to subckt %s, which declares "
                         "%d port(s)"
                         % (el.name, len(el.nodes), sub.name, len(sub.ports)),
                         el.where())

    def check_merged_nets(self):
        """Find pairs of named nets that are electrically the same node."""
        for a, b, where in self.deck.connects:
            self.merged.append((a, b, ".connect", where))
        for el in self.deck.elements:
            if el.kind != "R" or len(el.nodes) != 2:
                continue
            val = parse_value(el.tail[0]) if el.tail else None
            if val is None or val > self.short_ohms:
                continue
            self.merged.append((el.nodes[0], el.nodes[1],
                                "%s = %s ohm" % (el.name,
                                                 el.tail[0] if el.tail else "?"),
                                el.where()))
        for a, b, why, where in self.merged:
            if a in self.ground or b in self.ground:
                continue
            self.add(SEV_INFO, "merged-net",
                     "%s and %s are one node (%s)" % (a, b, why), where)

    def check_floating(self):
        for net, users in sorted(self.deck.net_users.items()):
            if net in self.ground or net in self.deck.globals:
                continue
            if any(rx.search(net) for rx in self.keep):
                continue
            if len(users) >= 2:
                continue
            el, idx = users[0]
            self.add(SEV_WARN, "floating-net",
                     "net %s is touched only by %s (port %d)"
                     % (net, el.name, idx), el.where())

    def check_unconnected_instances(self):
        """An X instance every one of whose nodes is otherwise unused."""
        for el in self.deck.elements:
            if el.kind != "X" or not el.nodes:
                continue
            lonely = [n for n in el.nodes
                      if len(self.deck.net_users.get(n, [])) < 2
                      and n not in self.ground and n not in self.deck.globals]
            if len(lonely) == len(el.nodes):
                self.add(SEV_ERROR, "isolated-instance",
                         "%s is connected to nothing else in the deck"
                         % el.name, el.where())

    def run(self):
        self.check_includes()
        self.check_unparsed()
        self.check_duplicate_names()
        self.check_instances()
        self.check_merged_nets()
        if self.netlist_incomplete and not self.force_connectivity:
            # Every element inside an unread file is missing from the graph,
            # so its nets look one-sided. Reporting hundreds of those as
            # findings would bury the one problem that caused them.
            n_float = sum(1 for net, users in self.deck.net_users.items()
                          if len(users) < 2 and net not in self.ground
                          and net not in self.deck.globals)
            self.add(SEV_WARN, "checks-skipped",
                     "connectivity checks (floating-net, isolated-instance) "
                     "skipped: the netlist is incomplete, and roughly %d net(s) "
                     "look one-sided purely because of that. Fix the includes "
                     "first, or pass --force-connectivity." % n_float)
        else:
            self.check_unconnected_instances()
            self.check_floating()
        self.findings.sort(key=lambda f: (_ORDER[f.severity], f.code))
        return self.findings

    def counts(self):
        out = {SEV_ERROR: 0, SEV_WARN: 0, SEV_INFO: 0}
        for f in self.findings:
            out[f.severity] += 1
        return out


def summarize(findings):
    """code -> (severity, count), most severe and most numerous first."""
    tally = {}
    for f in findings:
        key = (f.severity, f.code)
        tally[key] = tally.get(key, 0) + 1
    rows = [(sev, code, n) for (sev, code), n in tally.items()]
    rows.sort(key=lambda r: (_ORDER[r[0]], -r[2], r[1]))
    return rows


def render(deck, findings, counts, verbose=False, limit=10, summary_only=False):
    out = []
    out.append("primesim-dm-setup deck check")
    out.append("=" * 66)
    out.append("files    : %d" % len(deck.files))
    for f in deck.files:
        out.append("           %s" % f)
    out.append("elements : %d at the top level" % len(deck.elements))
    out.append("nets     : %d" % len(deck.net_users))
    out.append("subckts  : %d definition(s) available" % len(deck.subckts))
    if deck.opaque_files:
        out.append("opaque   : %d file(s) read for interfaces only "
                   "(insides not checked)" % len(deck.opaque_files))
    if deck.skipped_files:
        out.append("skipped  : %d file(s) not opened; subckts defined there "
                   "will read as undefined" % len(deck.skipped_files))
    if deck.depth_limited:
        out.append("depth cap: %d file(s) hit --max-depth; their includes "
                   "were not followed" % len(deck.depth_limited))
    if deck.unparsed:
        out.append("unparsed : %d line(s)  <- checks below do not cover these"
                   % len(deck.unparsed))
    out.append("")

    shown = findings if verbose else [f for f in findings
                                      if f.severity != SEV_INFO]

    rows = summarize(shown)
    if rows:
        out.append("by kind:")
        for sev, code, n in rows:
            out.append("  %-5s %-20s %5d" % (sev.upper(), code, n))
        out.append("")

    if summary_only:
        pass
    elif not shown:
        out.append("no problems found"
                   + ("" if verbose else " (re-run with -v for info notes)"))
    else:
        # cap each kind: one wrong assumption can produce hundreds of
        # identical findings, and that buries the ones that differ
        seen = {}
        hidden = {}
        for f in shown:
            seen[f.code] = seen.get(f.code, 0) + 1
            if limit and seen[f.code] > limit:
                hidden[f.code] = hidden.get(f.code, 0) + 1
                continue
            out.append(f.line())
        for code in sorted(hidden):
            out.append("      ... and %d more %s (use --all to list every one)"
                       % (hidden[code], code))
    out.append("")
    out.append("%d error(s), %d warning(s), %d note(s)"
               % (counts[SEV_ERROR], counts[SEV_WARN], counts[SEV_INFO]))
    return "\n".join(out) + "\n"
