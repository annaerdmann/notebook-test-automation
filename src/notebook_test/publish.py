"""`notebook-test dashboard`, `catalog` and `relabel`: turn run reports into a dashboard page and a catalog exclude list.

Usage:
    notebook-test dashboard REPORT.json [REPORT.json ...] --out dashboard.html
    notebook-test dashboard --serve --source FOLDER_OR_GIT_URL [--port 8765] [--cache DIR]
    notebook-test catalog REPORT.json --out catalog_exclude.json [--profile NAME_OR_FILE]
    notebook-test relabel REPORT.json --profile NAME_OR_FILE --out NEW.json

The dashboard shows the newest report in detail and the pass rate of every report given, so a
folder of quarterly reports becomes a history. With `--serve` the dashboard is an interactive page on
127.0.0.1 that reads all reports in a folder or git repository (see serve.py and status.py). The catalog list names notebooks that failed for
reasons the author has to fix (labels the profile marks `blocking`); service outages and
test-account credential problems do not count. Reports carry their labels; reports from before
schema_version 1 need `--profile`.
"""

import argparse
import html
import json
import sys
from pathlib import Path

from .labels import FOLLOW_ON, Labeler
from .profiles import ProfileError, load_profile
from .report import SCHEMA_VERSION, report_context, to_markdown


def _load(paths, profile=None):
    """Read reports. A report without labels (older format) gets them from `profile`."""
    reports = [json.loads(Path(p).read_text()) for p in paths]
    for r in reports:
        if "labels" not in r:
            p = load_profile(profile or "generic")
            r.update({k: v for k, v in report_context([p]).items() if k != "profile"})
            r["profile"] = p["name"]
    return sorted(reports, key=lambda r: r["started_at"])


def _text(report, label):
    return report["labels"].get(label, {}).get("text", label)


def _blocking(report, result):
    blocking = {k for k, v in report["labels"].items() if v.get("blocking")}
    return sorted({e["label"] for e in result["cell_errors"] if e["label"] in blocking})


def _github_link(repo, notebook):
    url, commit = repo.get("url"), repo.get("commit")
    if url and "github.com" in url:
        return f"{url.removesuffix('.git')}/blob/{commit or 'main'}/{notebook}"
    return None


def catalog_list(report):
    out = []
    for repo in report["repos"]:
        for r in repo["results"]:
            reasons = _blocking(report, r)
            if reasons:
                out.append({"repo": repo["repo"], "notebook": r["notebook"],
                            "link": _github_link(repo, r["notebook"]),
                            "reasons": [_text(report, x) for x in reasons]})
    return {"generated_from": report["started_at"], "host": report.get("host"),
            "note": report["texts"]["exclude_note"],
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
        blocking = {k for k, v in latest["labels"].items() if v.get("blocking")}
        chips = " ".join(f'<span class="chip {k}{" blocking" if k in blocking else ""}">{v} {e(_text(latest, k))}</span>' for k, v in sorted(labels.items()))
        rows.append(f"<tr><td>{e(repo['repo'])}</td><td class=num>{len(res)}</td><td class=num>{passed}</td>"
                    f"<td><div class=bar><span style='width:{pct}%'></span></div> {pct}%</td><td>{chips}</td></tr>")
        nb_rows = []
        for r in res:
            link = _github_link(repo, r["notebook"])
            name = f'<a href="{e(link)}">{e(r["notebook"])}</a>' if link else e(r["notebook"])
            kinds = ", ".join(f"{v} {_text(latest, k)}" for k, v in sorted(r["labels"].items()) if k != FOLLOW_ON)
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
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(latest['texts']['dashboard_title'])}</title>
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
.chip.blocking{{border-color:var(--bad)}}
.st.ok{{color:var(--ok)}} .st.error{{color:var(--bad)}} a{{color:var(--accent)}}
details{{border:1px solid var(--line);border-radius:8px;padding:6px 12px;margin:8px 0}} summary{{cursor:pointer;font-weight:600}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>{e(latest['texts']['dashboard_title'])}</h1>
<p class=sub>Latest run {e(latest['started_at'])} on {e(latest.get('host', ''))}. Follow-on errors are not counted.</p>
<div class=wrap><table><tr><th>Repository</th><th class=num>Notebooks</th><th class=num>Passed</th><th>Pass rate</th><th>Errors by type</th></tr>{''.join(rows)}</table></div>
<h2>Notebooks</h2><div class=wrap>{''.join(details)}</div>
<h2>History</h2><div class=wrap><table><tr><th>Run</th><th>Host</th><th class=num>Notebooks</th><th class=num>Passed</th></tr>{''.join(history)}</table></div>
</body></html>"""


def main_dashboard(argv=None):
    ap = argparse.ArgumentParser(prog="notebook-test dashboard")
    ap.add_argument("reports", nargs="*")
    ap.add_argument("--profile", help="profile for reports without labels (older format)")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--serve", action="store_true", help="serve the interactive dashboard on 127.0.0.1")
    ap.add_argument("--source", help="with --serve: folder of reports, or git URL of the results repository")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cache", default="~/.cache/notebook-test/results", help="where a git source is cloned")
    a = ap.parse_args(argv)
    if a.serve:
        if not a.source:
            ap.error("--serve needs --source")
        from .serve import serve
        return serve(a.source, a.port, a.cache)
    if not a.reports:
        ap.error("give report files, or --serve --source")
    Path(a.out).write_text(dashboard_html(_load(a.reports, a.profile)))
    print(f"Dashboard: {a.out}")
    return 0


def main_catalog(argv=None):
    ap = argparse.ArgumentParser(prog="notebook-test catalog")
    ap.add_argument("report")
    ap.add_argument("--profile", help="profile for a report without labels (older format)")
    ap.add_argument("--out", default="catalog_exclude.json")
    a = ap.parse_args(argv)
    data = catalog_list(_load([a.report], a.profile)[-1])
    Path(a.out).write_text(json.dumps(data, indent=2))
    print(f"{len(data['exclude'])} notebook(s) to keep out of the catalog: {a.out}")
    return 0


def relabel(report, profile):
    """Label the errors of an existing report again with another profile (for example after a rule change).

    follow-on labels are kept, because they depend on the cell source, which the report does not hold.
    """
    labeler = Labeler(profile)
    out = json.loads(json.dumps(report))
    for repo in out["repos"]:
        repo["profile"] = profile["name"]
        for r in repo["results"]:
            for e in r["cell_errors"]:
                if e["label"] != FOLLOW_ON:
                    e["label"] = labeler.label_error(e["error_name"], e["error_value"],
                                                     e["traceback_last_lines"] + "\n" + e["cell_preview"], set())
            r["labels"] = {}
            for e in r["cell_errors"]:
                r["labels"][e["label"]] = r["labels"].get(e["label"], 0) + 1
    out.update(report_context([profile]))
    return out


def main_relabel(argv=None):
    ap = argparse.ArgumentParser(prog="notebook-test relabel", description=relabel.__doc__)
    ap.add_argument("report")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        profile = load_profile(a.profile)
    except ProfileError as e:
        print(f"notebook-test: {e}", file=sys.stderr)
        return 2
    data = relabel(json.loads(Path(a.report).read_text()), profile)
    Path(a.out).write_text(json.dumps(data, indent=2))
    Path(a.out).with_suffix(".md").write_text(to_markdown(data))
    print(f"Relabelled report: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main_dashboard())
