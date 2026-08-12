"""Port -> net resolution, termination, and floating-net analysis.

This is where the actual "auto setup" happens:

  1. every ``.subckt`` port of every instance is bound to a net, using
     (in order) explicit ``connect``, instance rules, global naming rules,
     then the default policy;
  2. ports that are declared unused - or that a rule marks as such - get a
     termination element instead of a dangling name;
  3. after everything is bound, any net that only one thing touches is
     reported and (optionally) terminated too.
"""

import difflib
import re

from . import spice

_FIELD = re.compile(r"\{([^{}]+)\}")


class NetlistError(Exception):
    pass


def _apply_transform(value, name, aliases=None):
    if name.startswith("alias:"):
        table = name.split(":", 1)[1]
        return apply_alias(value, table, aliases)
    if name == "upper":
        return value.upper()
    if name == "lower":
        return value.lower()
    if name == "int":
        return str(int(value))
    if name.startswith("zfill:"):
        return value.zfill(int(name.split(":", 1)[1]))
    if name.startswith("strip:"):
        return value.strip(name.split(":", 1)[1])
    raise NetlistError("unknown template transform %r" % name)


def apply_alias(value, table, aliases):
    """Map a project-specific signal token onto the canonical name.

    The same JEDEC signal shows up as ``CA0`` in one model, ``SCA0`` or
    ``SA0`` in the next, so the mapping cannot be a regex in the wiring rule -
    it has to be a table someone maintains per project.
    """
    aliases = aliases or {}
    if table not in aliases:
        raise NetlistError("template uses alias table %r, which is not "
                           "defined under 'aliases'" % table)
    spec = aliases[table]
    for rx, to in spec["rules"]:
        m = rx.match(value)
        if m:
            return expand_template(to, m, {"in": value})
    if spec["on_miss"] == "keep":
        return value
    raise NetlistError(
        "signal %r matches no rule in alias table %r - add it, or set "
        "aliases.%s.on_miss to \"keep\"" % (value, table, table))


def expand_template(tmpl, match, ctx, aliases=None):
    """Expand ``{1}``/``{name}`` fields, with optional ``|transform`` chains."""

    def sub(mo):
        spec = mo.group(1)
        parts = spec.split("|")
        key = parts[0].strip()
        if key.isdigit():
            idx = int(key)
            if match is None or idx > (match.re.groups if match else 0):
                raise NetlistError(
                    "template %r refers to group %s which the match does not have"
                    % (tmpl, key))
            value = match.group(idx)
        elif match is not None and key in (match.groupdict() or {}):
            value = match.group(key)
        elif key in ctx:
            value = ctx[key]
        else:
            raise NetlistError("template %r refers to unknown field %r"
                               % (tmpl, key))
        value = "" if value is None else str(value)
        for t in parts[1:]:
            value = _apply_transform(value, t.strip(), aliases)
        return value

    tmpl = tmpl.replace("{{", "\x00").replace("}}", "\x01")
    out = _FIELD.sub(sub, tmpl)
    return out.replace("\x00", "{").replace("\x01", "}")


class Binding(object):
    """One resolved subckt port."""

    def __init__(self, inst, port, index, token=None):
        self.inst = inst
        self.port = port             # label: *node name if there is one
        self.token = token or port   # the raw token in the .subckt line
        self.index = index
        self.match_name = port
        self.net = None
        self.origin = "unresolved"   # explicit | rule | inst-rule | default | auto
        self.detail = ""
        self.terminated = False
        self.term = None             # termination spec dict


class TermElement(object):
    def __init__(self, kind, name, nodes, value, comment):
        self.kind = kind
        self.name = name
        self.nodes = nodes
        self.value = value
        self.comment = comment


class Netlist(object):
    def __init__(self):
        self.instances = []          # list of (inst_cfg, subckt, [Binding])
        self.term_elements = []
        self.warnings = []
        self.net_users = {}          # net -> list of "INST.PORT" strings
        self.open_nets = set()       # deliberately left open (type: open)
        self.auto_terminated = []    # bindings the floating pass loaded

    def nets(self):
        return sorted(self.net_users)


class Resolver(object):
    def __init__(self, cfg, subckt_index):
        self.cfg = cfg
        self.index = subckt_index
        self.naming = cfg["naming"]
        self.term_cfg = cfg["termination"]
        self.warnings = []
        self._global_rules = self._compile(self.naming["rules"], "naming.rules")
        self._term_overrides = self._compile(self.term_cfg["overrides"],
                                             "termination.overrides")
        self._keep = [re.compile(p) for p in self.term_cfg["keep_nets"]]
        self.aliases = self._compile_aliases(cfg["aliases"])
        self._counter = 0

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _compile_aliases(tables):
        out = {}
        for name, spec in (tables or {}).items():
            out[name] = {
                "on_miss": spec.get("on_miss", "error"),
                "rules": [(re.compile(r["match"]), r["to"])
                          for r in spec.get("rules", [])],
            }
        return out

    def _compile(self, rules, where):
        out = []
        for i, r in enumerate(rules or []):
            out.append((re.compile(r["match"]), r, "%s[%d]" % (where, i)))
        return out

    def warn(self, msg):
        self.warnings.append(msg)

    def _normalize_net(self, net):
        if net.startswith("!"):           # escape hatch: use verbatim
            return net[1:]
        net = spice.normalize_bus(net, self.naming["bus_style"])
        case = self.naming["case"]
        if case == "upper":
            net = net.upper()
        elif case == "lower":
            net = net.lower()
        return net

    def _uniq(self, stem):
        self._counter += 1
        return "%s%d" % (stem, self._counter)

    def _lookup_subckt(self, inst):
        name = inst["subckt"]
        cands = self.index.get(name.lower(), [])
        if not cands:
            close = difflib.get_close_matches(name.lower(), sorted(self.index),
                                              n=5, cutoff=0.6)
            close += [s for s in sorted(self.index)
                      if (name.lower() in s or s in name.lower())
                      and s not in close]
            hint = (" did you mean: %s?" % ", ".join(close[:5])) if close else ""
            raise NetlistError(
                "instance %s: subckt %r not found in the scanned model files.%s"
                % (inst["name"], name, hint))
        if len(cands) > 1:
            want = inst.get("source")
            if want:
                hits = [c for c in cands
                        if c.path.endswith(want) or want in c.path]
                if len(hits) == 1:
                    return hits[0]
                if not hits:
                    raise NetlistError(
                        "instance %s: subckt %r is defined in %d files, none "
                        "matching source %r:\n  %s"
                        % (inst["name"], name, len(cands), want,
                           "\n  ".join(c.path for c in cands)))
                raise NetlistError(
                    "instance %s: source %r still matches %d files - make it "
                    "more specific:\n  %s"
                    % (inst["name"], want, len(hits),
                       "\n  ".join(c.path for c in hits)))
            policy = self.cfg["models"]["on_duplicate"]
            msg = ("instance %s: subckt %r is defined in %d files, so which "
                   "one to use is a decision this tool will not make for you."
                   " Set 'source' on the instance to one of:\n  %s"
                   % (inst["name"], name, len(cands),
                      "\n  ".join(c.path for c in cands)))
            if policy == "error":
                raise NetlistError(msg)
            self.warn(msg + "\n  -> using the first")
        return cands[0]

    # -- termination -----------------------------------------------------
    def _term_anticipated(self, inst, port):
        """True if the config says something about terminating this port.

        Used to tell "an output nobody loads, and we said so" apart from "a
        link that silently failed to join up" - only the latter is worth a
        warning when the floating pass cleans it up.
        """
        if inst.get("termination"):
            return True
        for rx, _r, _where in self._term_overrides:
            if rx.search(port):
                return True
        return False

    def _term_spec(self, inst, port, rule=None):
        spec = dict(self.term_cfg["default"])
        for rx, r, _where in self._term_overrides:
            if rx.search(port):
                spec.update({k: v for k, v in r.items() if k != "match"})
                break
        if inst.get("termination"):
            spec.update(inst["termination"])
        if rule:
            for k in ("type", "to", "value", "value2"):
                if k in rule:
                    spec[k] = rule[k]
        return spec

    def _terminate(self, binding, spec, netlist):
        kind = spec.get("type", "rload")
        to = self._normalize_net(str(spec.get("to", "0")))
        binding.terminated = True
        binding.term = spec
        # ODT_EN<2> -> ODT_EN_2 so the auto net name stays readable
        flat = re.sub(r"[\[<\(](\w+)[\]>\)]", r"_\1", binding.match_name)
        stem = "%s_%s" % (binding.inst, re.sub(r"\W", "_", flat))

        if kind == "tie":
            binding.net = to
            return
        if kind == "open":
            if binding.net is None:
                binding.net = self._normalize_net(
                    self.term_cfg["net_prefix"] + stem)
            netlist.open_nets.add(binding.net)
            return

        if binding.net is None:
            binding.net = self._normalize_net(
                self.term_cfg["net_prefix"] + stem)
        node = binding.net
        if kind == "rload":
            netlist.term_elements.append(TermElement(
                "R", self._uniq("Rterm_"), [node, to],
                str(spec.get("value", "1T")), "%s.%s" % (binding.inst, binding.port)))
        elif kind == "cload":
            netlist.term_elements.append(TermElement(
                "C", self._uniq("Cterm_"), [node, to],
                str(spec.get("value", "1f")), "%s.%s" % (binding.inst, binding.port)))
        elif kind == "rc":
            netlist.term_elements.append(TermElement(
                "R", self._uniq("Rterm_"), [node, to],
                str(spec.get("value", "1T")), "%s.%s" % (binding.inst, binding.port)))
            netlist.term_elements.append(TermElement(
                "C", self._uniq("Cterm_"), [node, to],
                str(spec.get("value2", "1f")), "%s.%s" % (binding.inst, binding.port)))
        elif kind == "vsource":
            netlist.term_elements.append(TermElement(
                "V", self._uniq("Vterm_"), [node, to],
                "DC %s" % spec.get("value", "0"),
                "%s.%s" % (binding.inst, binding.port)))
        elif kind == "isource":
            netlist.term_elements.append(TermElement(
                "I", self._uniq("Iterm_"), [node, to],
                "DC %s" % spec.get("value", "0"),
                "%s.%s" % (binding.inst, binding.port)))
        else:
            raise NetlistError("unknown termination type %r" % kind)

    # -- per-port resolution --------------------------------------------
    def match_name(self, port):
        """The port name that rules and connect keys are matched against.

        With ``naming.match_normalized`` (the default) the bus delimiter is
        folded to ``naming.bus_style`` first, so one rule set works on models
        that write ``DQ[0]``, ``DQ<0>`` or ``DQ(0)``.  Case is left alone -
        rules are written against the model's spelling.
        """
        if not self.naming.get("match_normalized", True):
            return port
        return spice.normalize_bus(port, self.naming["bus_style"])

    def _resolve_port(self, inst, sub, port, index, inst_rules, explicit,
                      unused_rx, netlist):
        b = Binding(inst["name"], port, index, token=sub.ports[index])
        mport = self.match_name(port)
        b.match_name = mport
        ctx = {"port": mport, "raw_port": port, "inst": inst["name"],
               "subckt": sub.name, "index": str(index)}

        if mport in explicit:
            b.net = self._normalize_net(str(explicit[mport]))
            b.origin = "explicit"
            b.detail = "connect[%s]" % mport
            return b

        for rx in unused_rx:
            if rx.search(mport):
                b.origin = "unused"
                b.detail = "unused pattern %s" % rx.pattern
                self._terminate(b, self._term_spec(inst, mport), netlist)
                return b

        for rules, kind in ((inst_rules, "inst-rule"),
                            (self._global_rules, "rule")):
            for rx, rule, where in rules:
                m = rx.search(mport)
                if not m:
                    continue
                action = rule.get("action", "connect")
                if action == "skip":
                    continue
                if action == "terminate":
                    b.origin = kind
                    b.detail = "%s (terminate) %s" % (where, rule["match"])
                    if rule.get("net"):
                        b.net = self._normalize_net(
                            expand_template(rule["net"], m, ctx,
                                            self.aliases))
                    self._terminate(b, self._term_spec(inst, port, rule), netlist)
                    return b
                b.net = self._normalize_net(
                    expand_template(rule["net"], m, ctx, self.aliases))
                b.origin = kind
                b.detail = "%s %s" % (where, rule["match"])
                return b

        policy = inst.get("default", self.naming["default"])
        if policy == "same_name":
            b.net = self._normalize_net(mport)
            b.origin = "default"
            b.detail = "same_name"
        elif policy == "prefix":
            b.net = self._normalize_net(
                inst["name"] + self.naming["prefix_sep"] + mport)
            b.origin = "default"
            b.detail = "prefix"
        elif policy == "terminate":
            b.origin = "default"
            b.detail = "default terminate"
            self._terminate(b, self._term_spec(inst, mport), netlist)
        else:  # error
            raise NetlistError(
                "instance %s: port %r matched no rule and naming.default is "
                "'error'" % (inst["name"], port))
        return b

    # -- driver ----------------------------------------------------------
    def build(self):
        nl = Netlist()
        for inst in self.cfg["instances"]:
            sub = self._lookup_subckt(inst)
            # Rules and connect keys match the port *label*: normally the
            # token in the .subckt line, but for an S-parameter channel that
            # is a bare number, so the *node comment name is used instead.
            labels = sub.labels()
            for problem in sub.annotation_problems():
                self.warn(problem)
            port_keys = set(self.match_name(p) for p in labels)
            lower_keys = dict((k.lower(), k) for k in port_keys)
            explicit = {}
            for k, v in (inst.get("connect", {}) or {}).items():
                mk = self.match_name(str(k))
                if mk in port_keys:
                    explicit[mk] = v
                elif mk.lower() in lower_keys:
                    explicit[lower_keys[mk.lower()]] = v
                else:
                    self.warn("instance %s: connect[%r] does not match any port "
                              "of subckt %s" % (inst["name"], k, sub.name))

            inst_rules = self._compile(inst.get("rules", []),
                                       "instances[%s].rules" % inst["name"])
            unused_rx = [re.compile(p) for p in (inst.get("unused", []) or [])]

            bindings = []
            for i, label in enumerate(labels):
                bindings.append(self._resolve_port(
                    inst, sub, label, i, inst_rules, explicit, unused_rx, nl))
            nl.instances.append((inst, sub, bindings))

        self._index_nets(nl)
        self._floating_pass(nl)
        nl.warnings = list(self.warnings)
        return nl

    def _index_nets(self, nl):
        nl.net_users = {}
        for _inst, _sub, bindings in nl.instances:
            for b in bindings:
                nl.net_users.setdefault(b.net, []).append(
                    "%s.%s" % (b.inst, b.port))
        for el in nl.term_elements:
            for node in el.nodes:
                nl.net_users.setdefault(node, []).append(el.name)
        for sup in self.cfg["supplies"]:
            net = self._normalize_net(str(sup["net"]))
            nl.net_users.setdefault(net, []).append("supply")
        # raw / stimulus lines: count any token that is already a known net so
        # a hand-written stimulus does not show up as a floating node
        known = set(nl.net_users)
        raw_lines = (list(self.cfg["stimulus"]) + list(self.cfg["raw_prepend"])
                     + list(self.cfg["raw_append"]))
        for line in raw_lines:
            for tok in re.split(r"[\s,()=]+", str(line)):
                if tok in known:
                    nl.net_users.setdefault(tok, []).append("raw")

    def _is_kept(self, net):
        if net in ("0", "gnd", "GND"):
            return True
        for g in self.cfg["deck"]["globals"]:
            if self._normalize_net(str(g)) == net:
                return True
        for rx in self._keep:
            if rx.search(net):
                return True
        return False

    def _floating_pass(self, nl):
        floating = []
        for net, users in sorted(nl.net_users.items()):
            if len(users) >= 2 or self._is_kept(net) or net in nl.open_nets:
                continue
            floating.append((net, users[0]))
        if not floating:
            return
        auto = self.term_cfg["auto_terminate_floating"]
        for net, user in floating:
            if not auto:
                self.warn("floating net %s (only %s)" % (net, user))
                continue
            binding = self._find_binding(nl, user)
            if binding is None or binding.terminated:
                self.warn("floating net %s (only %s) - left as is" % (net, user))
                continue
            inst_cfg = self._inst_cfg(nl, binding.inst) or {}
            spec = self._term_spec(inst_cfg, binding.match_name)
            if spec.get("type") == "tie":
                spec = dict(spec)
                spec["type"] = "rload"     # never short a real signal to a rail
            self._terminate(binding, spec, nl)
            binding.origin = binding.origin + "+auto-term"
            binding.detail = (binding.detail + "; floating -> %s"
                              % spec.get("type"))
            nl.auto_terminated.append(binding)
            # nothing in the config anticipated this pin being unloaded, so it
            # is more likely a rule that failed to join two blocks up
            if not self._term_anticipated(inst_cfg, binding.match_name):
                self.warn("%s.%s -> %s was floating and got a %s; no rule or "
                          "override expected that - is the net name right?"
                          % (binding.inst, binding.port, net,
                             spec.get("type")))
        self._index_nets(nl)

    @staticmethod
    def _find_binding(nl, user):
        if "." not in user:
            return None
        inst, port = user.split(".", 1)
        for _i, _s, bindings in nl.instances:
            for b in bindings:
                if b.inst == inst and b.port == port:
                    return b
        return None

    def _inst_cfg(self, nl, name):
        for inst, _s, _b in nl.instances:
            if inst["name"] == name:
                return inst
        return None


def build_index(subckts):
    """name(lowercased) -> [Subckt]; top-level definitions come first."""
    index = {}
    for s in sorted(subckts, key=lambda x: x.depth):
        index.setdefault(s.name.lower(), []).append(s)
    return index
