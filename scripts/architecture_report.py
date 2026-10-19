"""Architecture metrics report for src/msianalyzer, as one HTML page.

Usage: python scripts/architecture_report.py [output.html]
(default: build/architecture_report.html)

Size and LCOM come from ArchUnitPython. Instability is computed here from our
own import graph: archunitpython 1.8.1's instability is a stub (Ca/Ce are never
filled in, every file gets I = 0.5), and its graph resolves
`from pkg import submodule` to pkg/__init__.py instead of the submodule.
"""

import ast
import datetime
import html
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from archunitpython.metrics.calculation.count import (
    ImportCountMetric,
    LinesOfCodeMetric,
    StatementCountMetric,
)
from archunitpython.metrics.calculation.lcom import LCOM4, LCOM96b
from archunitpython.metrics.extraction.extract_class_info import extract_class_info

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "msianalyzer"
# resources_rc.py: generated Qt resources; .ipynb_checkpoints: local, gitignored
EXCLUDE = ["__pycache__", ".ipynb_checkpoints", "resources_rc.py"]
TOP_FILES = 15
TOP_CLASSES = 15
MIN_METHODS = 5  # LCOM is noise on tiny classes


def module_name(path: Path) -> str:
    parts = ("msianalyzer",) + path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def package_of(path: Path) -> str:
    """Analysis unit for package metrics: up to two folders below msianalyzer."""
    parts = path.relative_to(SRC).parent.parts[:2]
    return "/".join(parts) or "(root)"


def import_targets(path: Path, modules: dict[str, Path]) -> set[Path]:
    """Internal files imported anywhere in `path` (function-level imports too)."""

    def resolve(name: str) -> Path | None:
        while name:
            if name in modules:
                return modules[name]
            name = name.rpartition(".")[0]
        return None

    own = module_name(path)
    package = own if path.name == "__init__.py" else own.rpartition(".")[0]
    targets = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            # `from pkg import sub` means the submodule when one exists.
            names = [f"{base}.{a.name}" if f"{base}.{a.name}" in modules else base for a in node.names]
        else:
            continue
        targets.update(t for n in names if n.startswith("msianalyzer") and (t := resolve(n)))
    targets.discard(path)
    return targets


def instability(ca: int, ce: int) -> float | None:
    return ce / (ca + ce) if ca + ce else None


def collect():
    files = sorted(p for p in SRC.rglob("*.py") if not any(x in p.parts or p.name == x for x in EXCLUDE))
    modules = {module_name(p): p for p in files}
    deps = {p: import_targets(p, modules) for p in files}

    fan_in = defaultdict(set)
    for src, targets in deps.items():
        for t in targets:
            fan_in[t].add(src)

    loc, stmts, imps = LinesOfCodeMetric(), StatementCountMetric(), ImportCountMetric()
    file_rows = []
    for p in files:
        ca, ce = len(fan_in[p]), len(deps[p])
        file_rows.append({
            "file": str(p.relative_to(SRC)), "layer": p.relative_to(SRC).parts[0],
            "loc": int(loc.calculate_from_file(str(p))), "stmts": int(stmts.calculate_from_file(str(p))),
            "imports": int(imps.calculate_from_file(str(p))), "ca": ca, "ce": ce, "i": instability(ca, ce),
        })

    pkg_out, pkg_in = defaultdict(set), defaultdict(set)
    pkg_files, pkg_loc = defaultdict(int), defaultdict(int)
    for row, p in zip(file_rows, files):
        pkg = package_of(p)
        pkg_files[pkg] += 1
        pkg_loc[pkg] += row["loc"]
        for t in deps[p]:
            if (tp := package_of(t)) != pkg:
                pkg_out[pkg].add(tp)
                pkg_in[tp].add(pkg)
    pkg_rows = [
        {"pkg": k, "layer": k.split("/")[0], "files": pkg_files[k], "loc": pkg_loc[k],
         "ca": len(pkg_in[k]), "ce": len(pkg_out[k]), "i": instability(len(pkg_in[k]), len(pkg_out[k]))}
        for k in pkg_files
    ]
    pkg_rows.sort(key=lambda r: (r["i"] is None, r["i"] or 0))

    class_rows = []
    for c in extract_class_info(str(SRC), exclude_patterns=EXCLUDE):
        if len(c.methods) >= MIN_METHODS:
            class_rows.append({
                "cls": c.name, "file": str(Path(c.file_path).relative_to(SRC)),
                "methods": len(c.methods), "fields": len(c.fields),
                "lcom96b": LCOM96b().calculate(c), "lcom4": int(LCOM4().calculate(c)),
            })
    class_rows.sort(key=lambda r: (-r["lcom96b"], -r["methods"]))

    edges = sum(len(t) for t in deps.values())
    return file_rows, pkg_rows, class_rows, edges


def fmt_i(i):
    return "–" if i is None else f"{i:.2f}"


def render(file_rows, pkg_rows, class_rows, edges) -> str:
    e = html.escape
    try:
        commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    today = datetime.date.today().isoformat()
    total_loc = sum(r["loc"] for r in file_rows)

    pkg_html = "\n".join(
        f'<tr><td class="name"><span class="dot {r["layer"]}"></span>{e(r["pkg"])}</td>'
        f'<td class="plot"><div class="track">'
        + ("" if r["i"] is None else f'<span class="mark {r["layer"]}" style="left:{r["i"] * 100:.1f}%"></span>')
        + f'</div></td><td class="num">{fmt_i(r["i"])}</td><td class="num">{r["ca"]}</td>'
        f'<td class="num">{r["ce"]}</td><td class="num">{r["files"]}</td><td class="num">{r["loc"]:,}</td></tr>'
        for r in pkg_rows
    )

    big = sorted(file_rows, key=lambda r: -r["loc"])[:TOP_FILES]
    max_loc = big[0]["loc"]
    file_html = "\n".join(
        f'<tr><td class="name"><span class="dot {r["layer"]}"></span><code>{e(r["file"])}</code></td>'
        f'<td class="bar"><span style="width:{r["loc"] / max_loc * 100:.1f}%"></span></td>'
        f'<td class="num">{r["loc"]:,}</td><td class="num">{r["stmts"]:,}</td><td class="num">{r["imports"]}</td>'
        f'<td class="num">{r["ca"]}</td><td class="num">{r["ce"]}</td><td class="num">{fmt_i(r["i"])}</td></tr>'
        for r in big
    )

    def lcom_chip(v):
        level = "hi" if v >= 0.8 else "mid" if v >= 0.6 else "lo"
        return f'<span class="chip {level}">{v:.2f}</span>'

    class_html = "\n".join(
        f'<tr><td class="name"><b>{e(r["cls"])}</b><br><code>{e(r["file"])}</code></td>'
        f'<td class="num">{lcom_chip(r["lcom96b"])}</td><td class="num">{r["lcom4"]}</td>'
        f'<td class="num">{r["methods"]}</td><td class="num">{r["fields"]}</td></tr>'
        for r in class_rows[:TOP_CLASSES]
    )

    return f"""<title>MSIAnalyzer Architecture Metrics</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: one reading column; each section is a measured table, layers colour-coded like a legend. */
:root {{
  --bg: #f6f7f8; --surface: #ffffff; --fg: #1c2328; --muted: #5d6870; --rule: #dde2e6;
  --accent: #0f6e7a; --core: #0f6e7a; --gui: #b0612a; --cli: #6b5aa8;
  --ok: #2f7d4f; --warn: #a8741a; --bad: #b23b3b;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  --body: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #12171a; --surface: #192024; --fg: #e3e8eb; --muted: #94a1a9; --rule: #2b353b;
  --accent: #4fb3bf; --core: #4fb3bf; --gui: #e0935a; --cli: #a797e0;
  --ok: #6cc28f; --warn: #e0b45a; --bad: #e07a7a; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #12171a; --surface: #192024; --fg: #e3e8eb; --muted: #94a1a9; --rule: #2b353b;
  --accent: #4fb3bf; --core: #4fb3bf; --gui: #e0935a; --cli: #a797e0;
  --ok: #6cc28f; --warn: #e0b45a; --bad: #e07a7a; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.55 var(--body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 32px 64px; }}
main {{ max-width: 960px; margin: 0 auto; display: grid; gap: 40px; }}
h1, h2 {{ font-family: var(--display); font-weight: 600; text-wrap: balance; margin: 0; line-height: 1.15; }}
h1 {{ font-size: 2rem; }}
h2 {{ font-size: 1.35rem; }}
p {{ margin: 0; max-width: 68ch; }}
section {{ display: grid; gap: 12px; min-width: 0; }}
.meta {{ color: var(--muted); font-family: var(--mono); font-size: .8rem; }}
.facts {{ display: flex; flex-wrap: wrap; gap: 8px 28px; font-family: var(--mono); font-size: .85rem; }}
.facts b {{ font-weight: 500; font-size: 1.1rem; display: block; color: var(--fg); }}
.facts span {{ color: var(--muted); }}
.rules {{ display: grid; gap: 8px; }}
.rule {{ display: flex; gap: 12px; align-items: baseline; background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 10px 14px; }}
.status {{ font-family: var(--mono); font-size: .75rem; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}
.status.pass {{ color: var(--ok); border: 1px solid var(--ok); }}
.status.note {{ color: var(--warn); border: 1px solid var(--warn); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: .85rem; color: var(--muted); }}
.scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
th {{ text-align: left; font-family: var(--mono); font-weight: 500; font-size: .7rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); padding: 10px 12px; border-bottom: 1px solid var(--rule); white-space: nowrap; }}
td {{ padding: 7px 12px; border-bottom: 1px solid var(--rule); vertical-align: middle; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }}
th.num {{ text-align: right; }}
.name {{ white-space: nowrap; }}
code {{ font-family: var(--mono); font-size: .8rem; color: var(--muted); }}
.dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }}
.dot.core, .mark.core {{ background: var(--core); }}
.dot.gui, .mark.gui {{ background: var(--gui); }}
.dot.cli, .mark.cli {{ background: var(--cli); }}
.dot.\\(root\\), .mark.\\(root\\) {{ background: var(--muted); }}
.plot {{ min-width: 180px; width: 40%; }}
.track {{ position: relative; height: 14px; background: linear-gradient(to right, var(--rule) 1px, transparent 1px) 0 0 / 25% 100%, linear-gradient(var(--rule), var(--rule)) 0 50% / 100% 1px no-repeat; border-right: 1px solid var(--rule); }}
.mark {{ position: absolute; top: 2px; width: 10px; height: 10px; border-radius: 50%; transform: translateX(-50%); }}
.axis {{ display: flex; justify-content: space-between; font-family: var(--mono); font-size: .7rem; color: var(--muted); }}
.bar {{ min-width: 120px; width: 22%; }}
.bar span {{ display: block; height: 8px; background: var(--accent); opacity: .55; border-radius: 2px; }}
.chip {{ padding: 1px 7px; border-radius: 4px; }}
.chip.hi {{ color: var(--bad); border: 1px solid var(--bad); }}
.chip.mid {{ color: var(--warn); border: 1px solid var(--warn); }}
.chip.lo {{ color: var(--ok); border: 1px solid var(--ok); }}
details {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 10px 14px; }}
summary {{ cursor: pointer; font-weight: 600; }}
summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
details ul {{ margin: 10px 0 0; padding-left: 20px; display: grid; gap: 6px; max-width: 72ch; }}
</style>
<main>
<header style="display:grid;gap:12px">
  <h1>MSIAnalyzer Architecture Metrics</h1>
  <p class="meta">src/msianalyzer · commit {e(commit)} · {today} · ArchUnitPython 1.8.1</p>
  <div class="facts">
    <div><b>{len(file_rows)}</b><span>Python files</span></div>
    <div><b>{total_loc:,}</b><span>lines of code</span></div>
    <div><b>{edges}</b><span>internal import edges</span></div>
    <div><b>{len(pkg_rows)}</b><span>packages</span></div>
  </div>
</header>

<section>
  <h2>Rules</h2>
  <div class="rules">
    <div class="rule"><span class="status pass">pass</span><p><b>Layers.</b> <code>core</code> imports neither <code>cli</code> nor <code>gui</code>; <code>cli</code> does not import <code>gui</code>.</p></div>
    <div class="rule"><span class="status note">1 known</span><p><b>Import cycles.</b> One accepted cycle: <code>core/plotting/heatmap.py</code> ⇄ <code>core/spatial/local_moran.py</code> (a lazy import inside a function). Any new cycle fails <code>tests/test_architecture.py</code>.</p></div>
  </div>
</section>

<section>
  <h2>Package instability</h2>
  <p>I = Ce / (Ca + Ce). Ca counts the other packages that import this one; Ce counts the packages it imports. Near 0 means stable: many packages depend on it, so it is costly to change. Near 1 means unstable: it depends on others and nothing depends on it. In a healthy layout, dependencies point from unstable packages toward stable ones, so <code>gui</code> sits on the right and <code>core/utils</code> on the left.</p>
  <div class="legend"><span><span class="dot core"></span>core</span><span><span class="dot gui"></span>gui</span><span><span class="dot cli"></span>cli</span></div>
  <div class="scroll"><table>
    <thead><tr><th>Package</th><th><div class="axis"><span>0 stable</span><span>0.5</span><span>1 unstable</span></div></th><th class="num">I</th><th class="num">Ca</th><th class="num">Ce</th><th class="num">Files</th><th class="num">LOC</th></tr></thead>
    <tbody>{pkg_html}</tbody>
  </table></div>
</section>

<section>
  <h2>Largest files</h2>
  <p>The {TOP_FILES} largest files by lines of code (non-blank, non-comment). Ca and Ce here count files, not packages. A large file with high Ca is the most expensive to change: it is big, and much of the codebase depends on it.</p>
  <div class="scroll"><table>
    <thead><tr><th>File</th><th></th><th class="num">LOC</th><th class="num">Stmts</th><th class="num">Imports</th><th class="num">Ca</th><th class="num">Ce</th><th class="num">I</th></tr></thead>
    <tbody>{file_html}</tbody>
  </table></div>
</section>

<section>
  <h2>Class cohesion</h2>
  <p>Classes with at least {MIN_METHODS} methods, least cohesive first. LCOM96b runs from 0 to 1: it is 0 when every method uses every attribute and approaches 1 when each attribute is used by a single method. LCOM4 counts groups of methods that share no attribute; any value above 1 hints that the class bundles several jobs. Showing {min(TOP_CLASSES, len(class_rows))} of {len(class_rows)}.</p>
  <div class="scroll"><table>
    <thead><tr><th>Class</th><th class="num">LCOM96b</th><th class="num">LCOM4</th><th class="num">Methods</th><th class="num">Fields</th></tr></thead>
    <tbody>{class_html}</tbody>
  </table></div>
</section>

<details>
  <summary>How these numbers are measured, and their limits</summary>
  <ul>
    <li>Imports are read statically from the source, including imports inside functions. Dynamic imports are not seen.</li>
    <li><code>gui/resources_rc.py</code> (generated Qt resources) is excluded.</li>
    <li>A package is a folder up to two levels below <code>msianalyzer</code>, so <code>core/annotation</code> and <code>gui/utils</code> count as packages, and files directly in <code>core/</code> count as package <code>core</code>.</li>
    <li>Instability is computed by this script. ArchUnitPython 1.8.1's own instability gives 0.5 for every file, so its abstractness and distance-from-main-sequence numbers are skipped too. Abstractness is about 0 almost everywhere in this codebase anyway.</li>
    <li>LCOM counts as fields only attributes set with a plain <code>self.x = …</code>. Annotated assignments, dataclass fields and Qt properties are missed. LCOM4 does not link methods that call each other, so delegating methods (Qt slots, for example) each form their own group and inflate the count.</li>
    <li>QML files are not analysed.</li>
  </ul>
</details>
</main>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "build" / "architecture_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(*collect()), encoding="utf-8")
    print(out)
