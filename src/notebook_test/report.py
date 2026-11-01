"""Write the test report of `notebook-test run` as JSON and Markdown.

The JSON format is documented in the README (section "Report format") and described by report.schema.json.
"""

import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .labels import FOLLOW_ON

SCHEMA_VERSION = 1
PLATFORMS = ("local", "wekeo-jupyterhub")


def detect_platform(configured=None):
    """`platform` from the config; else the JupyterHub when JUPYTERHUB_API_URL is set, else local."""
    if configured:
        return configured
    return "wekeo-jupyterhub" if os.environ.get("JUPYTERHUB_API_URL") else "local"


def tool_version():
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("notebook-test-automation")
    except PackageNotFoundError:
        return "unknown"


def repo_key(url, name=None):
    """Repository URL without `.git`, with lower-case host and owner (ssh URLs become https)."""
    if not url:
        return name or "unknown"
    url = url.strip().rstrip("/").removesuffix(".git")
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^(https?://)([^/]+)/([^/]+)(/.*)?$", url)
    if m:
        return f"{m.group(1)}{m.group(2).lower()}/{m.group(3).lower()}{m.group(4) or ''}"
    return url


def notebook_id(url, name, notebook):
    return f"{repo_key(url, name)}/{notebook}"


def _plain(obj):
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def report_context(profiles):
    """Labels and texts of the profiles used, so readers of the JSON need no profile."""
    labels = {}
    for p in profiles:
        labels.update(p["labels"])
    first = profiles[0]
    return {"schema_version": SCHEMA_VERSION, "profile": first["name"], "labels": labels,
            "texts": {"title": first["report"]["title"], "dashboard_title": first["report"]["dashboard_title"],
                      "error_types_note": " ".join(first["report"]["error_types_note"].split()),
                      "protect_note": first["report"]["protect_note"], "exclude_note": first["exclude"]["note"]}}


def _text(run, label):
    return run["labels"].get(label, {}).get("text", label)


def _md_cell(text, limit=160):
    text = " ".join(str(text).split())
    text = (text[:limit] + "…") if len(text) > limit else text
    return text.replace("|", "\\|").replace("`", "'")


def to_markdown(run):
    lines = [f"# {run['texts']['title']}", "",
             f"Run: {run['started_at']} to {run['finished_at']} on `{run['host']}`", "",
             "## Summary", "",
             "| Repository | Commit | Notebooks | Passed | Failed | Errors by type |",
             "|---|---|---|---|---|---|"]
    for repo in run["repos"]:
        res = repo["results"]
        labels = {}
        for r in res:
            for k, v in r["labels"].items():
                labels[k] = labels.get(k, 0) + v
        by_type = ", ".join(f"{v} {_text(run, k)}" for k, v in sorted(labels.items())) or "–"
        commit = (repo.get("commit") or "")[:7]
        lines.append(f"| {repo['repo']} | {commit} | {len(res)} | {sum(r['status'] == 'ok' for r in res)} | "
                     f"{sum(r['status'] == 'error' for r in res)} | {by_type} |")
    lines += ["", run["texts"]["error_types_note"], ""]

    overwrote = [(repo["repo"], r) for repo in run["repos"] for r in repo["results"] if r.get("changed_credentials")]
    if overwrote:
        lines += ["## Notebooks that overwrote credential files", "",
                  run["texts"]["protect_note"], ""]
        lines += [f"- {name}: `{r['notebook']}` changed {', '.join(r['changed_credentials'])}" for name, r in overwrote]
        lines.append("")

    for repo in run["repos"]:
        lines += [f"## {repo['repo']}", "", "| Notebook | Status | Duration (s) | Errors | Rerun |", "|---|---|---|---|---|"]
        for r in repo["results"]:
            lines.append(f"| `{r['notebook']}` | {'✔' if r['status'] == 'ok' else '✖'} | {r['duration_s']} | "
                         f"{len(r['cell_errors'])} | {'yes' if r['reran'] else ''} |")
        for r in repo["results"]:
            real = [e for e in r["cell_errors"] if e["label"] != FOLLOW_ON]
            follow = len(r["cell_errors"]) - len(real)
            if not r["cell_errors"]:
                continue
            lines += ["", f"### `{r['notebook']}`", ""]
            lines += ["| Cell | Type | Error | Code |", "|---|---|---|---|"]
            for e in real:
                lines.append(f"| {e['cell_number']} | {_text(run, e['label'])} | "
                             f"{_md_cell(e['error_name'] + ': ' + e['error_value'])} | `{_md_cell(e['cell_preview'], 60)}` |")
            if follow:
                lines.append(f"\n{follow} follow-on error(s) not listed.")
        lines.append("")
    return "\n".join(lines)


def write_reports(run, out_dir, profiles, name=None):
    """Write the JSON and Markdown report. Never overwrites: a second run on the same day gets the
    start time added to its name, and an explicit `name` gets a number added if it is taken."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _plain({**report_context(profiles), **run})
    if name:
        stems = [name] + [f"{name}_{i}" for i in range(2, 1000)]
    else:
        day, clock = data["started_at"][:10], data["started_at"][11:19].replace(":", "")
        stems = [f"notebook_test_report_{day}", f"notebook_test_report_{day}_{clock}"]
        stems += [f"notebook_test_report_{day}_{clock}_{i}" for i in range(2, 1000)]
    stem = next(s for s in stems if not (out_dir / f"{s}.json").exists() and not (out_dir / f"{s}.md").exists())
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(data, indent=2))
    md_path.write_text(to_markdown(data))
    return [json_path, md_path]
