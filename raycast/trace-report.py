#!/usr/bin/env python3

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Trace Report
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🧾
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "trace.json (latest)", "optional": true }
# Runs perfetto-queries.sql against a gpu-trace.sh trace and writes a compact
# markdown report next to it (and to the clipboard) to hand to an AI agent.
# Defaults to the newest ~/Downloads/gpu-trace-*.json.

import csv
import datetime
import glob
import io
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
QUERIES = os.path.join(HERE, "perfetto-queries.sql")
PERFETTO_DIR = os.path.expanduser("~/.local/share/perfetto")
LAUNCHER = os.path.join(PERFETTO_DIR, "trace_processor")
MAX_ROWS = 40
# Columns that only matter for clicking rows in the Perfetto UI
HIDDEN = {"ts", "dur", "track_id"}

PAGE = """with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
)"""

META = PAGE + """
select
  round((trace_end() - trace_start()) / 1e9, 2) as duration_s,
  (
    select a.display_value from slice s join args a using (arg_set_id)
    join thread_track tt on s.track_id = tt.id join thread t using (utid)
    where t.upid = (select upid from page) and t.name = 'CrRendererMain'
      and a.display_value glob 'http*'
    group by 1 order by count(*) desc limit 1
  ) as url,
  (select count(*) from cpu_profile_stack_sample) as js_samples;
"""

ABOUT = """\
Startup trace of a page load, recorded by launching Chrome with
`--trace-config-file` (GPU, compositor, frame, Blink and V8 categories, plus
V8 CPU samples). Recording starts at browser launch, so the first few hundred
ms are Chrome booting.

How to read it:
- Times (`at_ms`, `from_ms`) are ms from trace start. `self_ms` is time spent
  in a slice or function itself, excluding its children.
- "Your page" is the renderer process with the most main-thread work.
- Frame states come from Chrome's PipelineReporter: `STATE_DROPPED` means a
  frame the page wanted to show was not presented.
- `CrRendererMain` is the page's main thread (JS, style, layout, paint).
  `CrGpuMain` is the GPU process main thread, which runs the page's GPU
  commands (`CommandBufferService:PutChanged`). `DawnPlatformImpl::RunWorkerTask`
  is shader/pipeline compilation on worker threads.
- JS function names and sources (sections 8 and 9) come from V8 CPU samples.
  Sources are bundled chunk URLs; search the codebase for the function name.
- `id` columns are Perfetto slice ids, usable for follow-up queries.
"""


def trace_processor():
    for name in ("trace_processor_shell", "trace_processor"):
        path = shutil.which(name)
        if path:
            return [path]
    # Perfetto's official launcher; it downloads and checksums the binary
    if not os.path.exists(LAUNCHER):
        os.makedirs(PERFETTO_DIR, exist_ok=True)
        subprocess.run(
            ["curl", "-sSfL", "-o", LAUNCHER, "https://get.perfetto.dev/trace_processor"],
            check=True,
        )
    return ["python3", LAUNCHER]


def sections(sql):
    """Split perfetto-queries.sql into (title, query) on its '-- N. ' headers."""
    for part in re.split(r"^(?=-- \d+\. )", sql, flags=re.M)[1:]:
        title = part.splitlines()[0][3:].strip().rstrip(".")
        yield title, part.strip()


def run(tp, trace, queries):
    """Run all queries in one trace_processor load; return {key: rows}."""
    script = "".join(
        "select '@@{}' as marker;\n{}\n".format(key, q) for key, q in queries
    )
    path = trace + ".report.sql"
    with open(path, "w") as f:
        f.write(script)
    try:
        proc = subprocess.run(tp + ["-q", path, trace], capture_output=True, text=True)
    finally:
        os.remove(path)

    results, key, rows = {}, None, []
    reader = csv.reader(io.StringIO(proc.stdout))
    for row in reader:
        if row == ["marker"]:
            if key is not None:
                results[key] = rows
            key, rows = next(reader)[0][2:], []
        elif row:
            rows.append(row)
    if key is not None:
        results[key] = rows
    error = next((l for l in proc.stderr.splitlines() if "rror" in l and "mport" not in l), None)
    return results, error


def cell(value):
    if value == "[NULL]":
        return ""
    try:
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return "{:.1f}".format(number)
    except ValueError:
        return value.replace("|", "\\|")


def table(rows):
    if len(rows) < 2:
        return "_No rows._"
    header, body = rows[0], rows[1:]
    keep = [i for i, name in enumerate(header) if name not in HIDDEN]
    lines = [
        "| " + " | ".join(header[i] for i in keep) + " |",
        "|" + "---|" * len(keep),
    ]
    for row in body[:MAX_ROWS]:
        lines.append("| " + " | ".join(cell(row[i]) for i in keep) + " |")
    if len(body) > MAX_ROWS:
        lines.append("\n_{} more rows not shown._".format(len(body) - MAX_ROWS))
    return "\n".join(lines)


def main():
    trace = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    if not trace:
        traces = sorted(glob.glob(os.path.expanduser("~/Downloads/gpu-trace-*.json")), key=os.path.getmtime)
        if not traces:
            print("No ~/Downloads/gpu-trace-*.json found")
            return 1
        trace = traces[-1]
    trace = os.path.abspath(os.path.expanduser(trace))
    if not os.path.isfile(trace):
        print("Missing " + trace)
        return 1

    with open(QUERIES) as f:
        queries = list(sections(f.read()))
    tp = trace_processor()
    results, error = run(tp, trace, [("meta", META)] + [(str(i), q) for i, (_, q) in enumerate(queries)])

    meta = dict(zip(*results["meta"])) if len(results.get("meta", [])) == 2 else {}
    out = [
        "# Chrome GPU trace report",
        "",
        "- Page: {}".format(meta.get("url") or "unknown"),
        "- Trace: `{}` ({}s)".format(trace, cell(meta.get("duration_s", "?"))),
        "- Generated: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
        "",
        ABOUT,
    ]
    if meta.get("js_samples") == "0":
        out.append("> This trace has no V8 CPU samples, so sections 8 and 9 are empty.\n")
    for i, (title, _) in enumerate(queries):
        rows = results.get(str(i))
        out += ["## " + title, "", table(rows) if rows is not None else "_Query failed: {}_".format(error), ""]
    out += [
        "## Digging deeper",
        "",
        "Query the raw trace with Perfetto's trace processor:",
        "",
        "```bash",
        "{} -q query.sql '{}'".format(" ".join(tp), trace),
        "```",
        "",
        "The queries behind each section are in `{}`.".format(QUERIES),
        "",
    ]
    report = "\n".join(out)

    path = re.sub(r"\.json$", "", trace) + ".md"
    with open(path, "w") as f:
        f.write(report)
    subprocess.run(["pbcopy"], input=report, text=True)
    print("Report copied to clipboard ({})".format(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
