"""Runs inside Blender for the `blend-diff` wrapper: diff the open file against another .blend."""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare_merge import api, diff  # noqa: E402

ap = argparse.ArgumentParser(prog="blend-diff")
ap.add_argument("incoming")
ap.add_argument("--all", action="store_true", help="also list identical data-blocks")
ap.add_argument("--json", action="store_true", help="print JSON instead of text")
ap.add_argument("--limit", type=int, default=12, help="changed fields shown per data-block")
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

entries = api.start(os.path.abspath(a.incoming))
if a.json:
    out = json.dumps({
        "stat": diff.stat(entries),
        "entries": [{"type": e.cat, "status": e.status, "current": e.current or None, "incoming": e.incoming or None,
                     "changes": [{"field": f, "current": x, "incoming": y} for f, x, y in e.changes]}
                    for e in entries if a.all or e.status != diff.SAME]}, indent=2)
else:
    out = api.text_report(entries, show_same=a.all, limit=a.limit)
api.close()
with open(os.environ["CM_RESULT"], "w") as fh:
    fh.write(out + "\n")
sys.exit(2 if any(e.status != diff.SAME for e in entries) else 0)
