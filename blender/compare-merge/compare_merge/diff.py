"""Two-way diff of snapshots, git style. Pure Python: no bpy, so it runs anywhere.

Entities are matched by type and name. Leftovers on both sides with identical content are paired
as renames (git's rename detection, with a 100% similarity threshold).
"""

from dataclasses import dataclass, field

SAME = "SAME"
CHANGED = "CHANGED"
RENAMED = "RENAMED"
ONLY_CURRENT = "ONLY_CURRENT"
ONLY_INCOMING = "ONLY_INCOMING"

STATUS_ORDER = {CHANGED: 0, RENAMED: 1, ONLY_INCOMING: 2, ONLY_CURRENT: 3, SAME: 4}
# Letter shown in the list, like `git status --short`.
STATUS_LETTER = {CHANGED: "M", RENAMED: "R", ONLY_INCOMING: "+", ONLY_CURRENT: "−", SAME: " "}


@dataclass
class Entry:
    cat: str
    current: str  # name in the current file ("" if only incoming)
    incoming: str  # name in the incoming file ("" if only current)
    status: str
    changes: list = field(default_factory=list)  # [(field, current value, incoming value)]

    @property
    def name(self):
        if self.status == RENAMED:
            return f"{self.current} → {self.incoming}"
        return self.current or self.incoming


def compare_fields(a, b):
    """[(field, a value, b value)] for every field that differs, in a stable, readable order."""
    out = []
    for k in list(a) + [k for k in b if k not in a]:
        va, vb = a.get(k, "—"), b.get(k, "—")
        if va != vb:
            out.append((k, va, vb))
    return out


def fingerprint(fields):
    return tuple(sorted(fields.items()))


def diff(current, incoming, order=None):
    """current / incoming: {(cat, name): {field: str}} -> [Entry] sorted by type, status, name."""
    entries = []
    only_c = {}
    only_i = {}
    for key, fields in current.items():
        if key in incoming:
            changes = compare_fields(fields, incoming[key])
            entries.append(Entry(key[0], key[1], key[1], CHANGED if changes else SAME, changes))
        else:
            only_c[key] = fields
    for key, fields in incoming.items():
        if key not in current:
            only_i[key] = fields

    # Rename detection: identical content, different name, same type, unambiguous on both sides.
    by_print = {}
    for (cat, name), fields in only_i.items():
        by_print.setdefault((cat, fingerprint(fields)), []).append(name)
    claimed = {}
    for (cat, name), fields in only_c.items():
        claimed.setdefault((cat, fingerprint(fields)), []).append(name)
    for k, cur_names in claimed.items():
        inc_names = by_print.get(k, [])
        if len(cur_names) == 1 and len(inc_names) == 1 and k[1]:
            cat = k[0]
            entries.append(Entry(cat, cur_names[0], inc_names[0], RENAMED))
            del only_c[(cat, cur_names[0])]
            del only_i[(cat, inc_names[0])]

    entries += [Entry(cat, name, "", ONLY_CURRENT) for (cat, name) in only_c]
    entries += [Entry(cat, "", name, ONLY_INCOMING) for (cat, name) in only_i]
    order = order or {}
    entries.sort(key=lambda e: (order.get(e.cat, 99), STATUS_ORDER[e.status], e.name.lower()))
    return entries


def stat(entries):
    """Counts per status, like `git diff --stat`'s summary line."""
    out = {s: 0 for s in STATUS_ORDER}
    for e in entries:
        out[e.status] += 1
    return out
