"""`wnqa dashboard` and `wnqa catalog`: turn run reports into a dashboard page and a catalog exclude list.

Usage:
    wnqa dashboard REPORT.json [REPORT.json ...] --out dashboard.html
    wnqa catalog REPORT.json --out catalog_exclude.json

The dashboard shows the newest report in detail and the pass rate of every report given, so a
folder of quarterly reports becomes a history. The catalog list names notebooks that failed for
reasons the author has to fix; service outages and test-account credential problems do not count.
"""

import argparse
import html
import json
import sys
from pathlib import Path

from .labels import FOLLOW_ON, KERNEL, LABEL_TEXT, NOTEBOOK, PACKAGE, TIMEOUT

# Labels that make a notebook unfit for the catalog.
BLOCKING = {NOTEBOOK, PACKAGE, TIMEOUT, KERNEL}


def _load(paths):
    reports = [json.loads(Path(p).read_text()) for p in paths]
    return sorted(reports, key=lambda r: r["started_at"])


def _blocking(result):
    return sorted({e["label"] for e in result["cell_errors"] if e["label"] in BLOCKING})


def _github_link(repo, notebook):
    url, commit = repo.get("url"), repo.get("commit")
    if url and "github.com" in url:
        return f"{url.removesuffix('.git')}/blob/{commit or 'main'}/{notebook}"
    return None


def catalog_list(report):
    out = []
    for repo in report["repos"]:
        for r in repo["results"]:
            reasons = _blocking(r)
            if reasons:
                out.append({"repo": repo["repo"], "notebook": r["notebook"],
                            "link": _github_link(repo, r["notebook"]),
                            "reasons": [LABEL_TEXT[x] for x in reasons]})
    return {"generated_from": report["started_at"], "host": report.get("host"),
            "note": "Notebooks that failed for reasons the author has to fix. Only valid if the run was on the WEkEO JupyterHub.",
            "exclude": out}


def dashboard_html(reports):
    latest = reports[-1]
    e = html.escape
    rows, details = [], []
    for repo in latest["repos"]:
        res = repo["results"]
        passed = sum(r["status"] == "ok" for r in res)
        labels = {}
        for r in res:
            for k, v in r["labels"].items():
                if k != FOLLOW_ON:
                    labels[k] = labels.get(k, 0) + v
        pct = round(100 * passed / len(res)) if res else 0
        chips = " ".join(f'<span class="chip {k}">{v} {e(LABEL_TEXT[k])}</span>' for k, v in sorted(labels.items()))
        rows.append(f"<tr><td>{e(repo['repo'])}</td><td class=num>{len(res)}</td><td class=num>{passed}</td>"
                    f"<td><div class=bar><span style='width:{pct}%'></span></div> {pct}%</td><td>{chips}</td></tr>")
        nb_rows = []
        for r in res:
            link = _github_link(repo, r["notebook"])
            name = f'<a href="{e(link)}">{e(r["notebook"])}</a>' if link else e(r["notebook"])
            kinds = ", ".join(f"{v} {LABEL_TEXT[k]}" for k, v in sorted(r["labels"].items()) if k != FOLLOW_ON)
            first = next((c for c in r["cell_errors"] if c["label"] != FOLLOW_ON), None)
            msg = e(f"cell {first['cell_number']}: {first['error_name']}: {first['error_value']}"[:160]) if first else ""
            nb_rows.append(f"<tr><td>{name}</td><td class='st {r['status']}'>{'pass' if r['status'] == 'ok' else 'fail'}</td>"
                           f"<td>{e(kinds)}</td><td class=msg>{msg}</td></tr>")
        details.append(f"<details><summary>{e(repo['repo'])}</summary><table><tr><th>Notebook</th><th>Result</th>"
                       f"<th>Errors</th><th>First error</th></tr>{''.join(nb_rows)}</table></details>")
    history = []
    for rep in reports:
        res = [r for repo in rep["repos"] for r in repo["results"]]
        ok = sum(r["status"] == "ok" for r in res)
        history.append(f"<tr><td>{e(rep['started_at'][:10])}</td><td>{e(rep.get('host', ''))}</td>"
                       f"<td class=num>{len(res)}</td><td class=num>{ok}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Notebook Test Dashboard</title>
<style>
:root{{--bg:#fff;--fg:#1c2430;--muted:#5b6675;--line:#e3e7ec;--ok:#1f7a4d;--bad:#b42318;--accent:#1a4696}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12161c;--fg:#e6e9ee;--muted:#9aa4b2;--line:#2a313b;--ok:#4cc38a;--bad:#f2766b;--accent:#7aa2ff}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin:0 0 24px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}} th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:13px}} .num{{text-align:right}} .msg{{color:var(--muted);font-size:13px}}
.bar{{display:inline-block;width:90px;height:8px;background:var(--line);border-radius:4px;vertical-align:middle}}
.bar span{{display:block;height:8px;background:var(--ok);border-radius:4px}}
.chip{{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin:1px}}
.chip.notebook_error,.chip.missing_package{{border-color:var(--bad)}}
.st.ok{{color:var(--ok)}} .st.error{{color:var(--bad)}} a{{color:var(--accent)}}
details{{border:1px solid var(--line);border-radius:8px;padding:6px 12px;margin:8px 0}} summary{{cursor:pointer;font-weight:600}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>Notebook test dashboard</h1>
<p class=sub>Latest run {e(latest['started_at'])} on {e(latest.get('host', ''))}. Follow-on errors are not counted.</p>
<div class=wrap><table><tr><th>Repository</th><th class=num>Notebooks</th><th class=num>Passed</th><th>Pass rate</th><th>Errors by type</th></tr>{''.join(rows)}</table></div>
<h2>Notebooks</h2><div class=wrap>{''.join(details)}</div>
<h2>History</h2><div class=wrap><table><tr><th>Run</th><th>Host</th><th class=num>Notebooks</th><th class=num>Passed</th></tr>{''.join(history)}</table></div>
</body></html>"""


def main_dashboard(argv=None):
    ap = argparse.ArgumentParser(prog="wnqa dashboard")
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--out", default="dashboard.html")
    a = ap.parse_args(argv)
    Path(a.out).write_text(dashboard_html(_load(a.reports)))
    print(f"Dashboard: {a.out}")
    return 0


def main_catalog(argv=None):
    ap = argparse.ArgumentParser(prog="wnqa catalog")
    ap.add_argument("report")
    ap.add_argument("--out", default="catalog_exclude.json")
    a = ap.parse_args(argv)
    data = catalog_list(_load([a.report])[-1])
    Path(a.out).write_text(json.dumps(data, indent=2))
    print(f"{len(data['exclude'])} notebook(s) to keep out of the catalog: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main_dashboard())
