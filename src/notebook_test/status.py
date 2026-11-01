"""Status and issues computed from a folder of run reports (used by the dashboard).

Nothing is stored: every number is derived from the reports, so deleting a report changes the result.

    reports = load_reports(folder)
    status = build_status(reports)
    issues = build_issues(reports)

Reports are found anywhere below the folder. Older reports without `platform` or `notebook_id` get
them from the folder name (`reports/<platform>/...`) and from the repository URL and notebook path.
"""

import hashlib
import json
from pathlib import Path

from .labels import FOLLOW_ON
from .report import PLATFORMS, notebook_id

LOCAL, JH = PLATFORMS
PLATFORM_LABEL = "platform"  # label of notebooks that need a JupyterHub service
MAX_ERROR = 300


def _platform_from_path(path, folder):
    for part in reversed(Path(path).relative_to(folder).parts[:-1]):
        if part in PLATFORMS:
            return part
    return None


def load_reports(folder):
    """Read every run report below `folder`, oldest first. Other JSON files are ignored."""
    folder = Path(folder)
    reports = []
    for path in sorted(folder.rglob("*.json")):
        if any(p.startswith(".") for p in path.relative_to(folder).parts):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("repos"), list) or "started_at" not in data:
            continue
        data["platform"] = data.get("platform") or _platform_from_path(path, folder) or LOCAL
        data["_file"] = str(path.relative_to(folder))
        for repo in data["repos"]:
            for r in repo.get("results", []):
                r.setdefault("notebook_id", notebook_id(repo.get("url"), repo.get("repo"), r["notebook"]))
        reports.append(data)
    return sorted(reports, key=lambda r: (r["started_at"], r["_file"]))


def _first_error(result):
    """The first error that is not a follow-on error (else the first one), or None."""
    errors = result.get("cell_errors") or []
    return next((e for e in errors if e["label"] != FOLLOW_ON), errors[0] if errors else None)


def _github_link(repo, notebook):
    url, commit = repo.get("url"), repo.get("commit")
    if url and "github.com" in url:
        return f"{url.removesuffix('.git')}/blob/{commit or 'main'}/{notebook}"
    return None


def _entries(reports):
    """Every notebook result as a flat record, oldest first."""
    out = []
    for rep in reports:
        for repo in rep["repos"]:
            for r in repo.get("results", []):
                out.append({"report": rep, "repo": repo, "result": r, "id": r["notebook_id"],
                            "platform": rep["platform"], "date": rep["started_at"][:10]})
    return out


def _labels(reports):
    texts = {}
    for rep in reports:
        for k, v in (rep.get("labels") or {}).items():
            texts[k] = v.get("text", k)
    return texts


def _issue_id(notebook, platform, error):
    key = "|".join([notebook, platform, error["label"], error["error_name"]])
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def build_issues(reports):
    """One issue per notebook, platform, label and error name, from the first non-follow-on error of
    each failed notebook. It is open while the newest run of that platform still shows it, and closed
    when a later run passed or showed another error."""
    series = {}
    for e in _entries(reports):
        series.setdefault((e["id"], e["platform"]), []).append(e)
    issues = {}
    for (nid, platform), runs in series.items():
        current = None  # id of the open issue
        for e in runs:
            r = e["result"]
            err = _first_error(r) if r["status"] == "error" else None
            iid = _issue_id(nid, platform, err) if err else None
            if current and current != iid:
                issues[current]["closed_at"] = e["date"]
                issues[current]["commits_between"] = {"last_failing": issues[current]["_commit"],
                                                      "closing": e["repo"].get("commit")}
                current = None
            if not err:
                continue
            issue = issues.setdefault(iid, {
                "id": iid, "notebook_id": nid, "repo": r["repo"], "notebook": r["notebook"], "platform": platform,
                "label": err["label"], "error_name": err["error_name"], "first_seen": e["date"],
                "link": _github_link(e["repo"], r["notebook"])})
            issue.update({"error_value": err["error_value"][:MAX_ERROR], "cell_number": err["cell_number"],
                          "last_seen": e["date"], "closed_at": None, "commits_between": None,
                          "report": e["report"]["_file"], "_commit": e["repo"].get("commit")})
            current = iid
    out = []
    for i in issues.values():
        i.pop("_commit")
        i["state"] = "closed" if i["closed_at"] else "open"
        out.append(i)
    return sorted(out, key=lambda i: (i["state"] != "open", i["first_seen"], i["id"]))


def _cell(e):
    r = e["result"]
    err = _first_error(r)
    return {"status": r["status"], "date": e["date"], "labels": {k: v for k, v in (r.get("labels") or {}).items()
                                                                 if k != FOLLOW_ON},
            "error": f"cell {err['cell_number']}: {err['error_name']}: {err['error_value'][:MAX_ERROR]}" if err else None,
            "commit": e["repo"].get("commit"), "env_source": r.get("env_source")}


def _rate(cells):
    """Pass numbers for a list of (cell, not_applicable) pairs."""
    cells = list(cells)
    applicable = [c for c, na in cells if not na]
    passed = sum(c["status"] == "ok" for c, _ in cells)
    ok = sum(c["status"] == "ok" for c in applicable)
    return {"passed": passed, "total": len(cells), "applicable": len(applicable), "applicable_passed": ok,
            "pct": round(100 * ok / len(applicable), 1) if applicable else None}


def build_status(reports):
    """Everything the dashboard shows: overall and per-repository pass rates, every notebook with its
    newest result per platform, and the pass rate over time."""
    entries = _entries(reports)
    latest = {}  # (notebook id, platform) -> newest entry
    for e in entries:
        latest[(e["id"], e["platform"])] = e
    ids = sorted({k[0] for k in latest})
    issues = build_issues(reports)
    open_by_nb = {}
    for i in issues:
        if i["state"] == "open":
            open_by_nb.setdefault(i["notebook_id"], []).append(i["id"])

    notebooks = []
    for nid in ids:
        found = {p: latest.get((nid, p)) for p in PLATFORMS}
        any_e = next(e for e in found.values() if e)
        na = any(e and any(c["label"] == PLATFORM_LABEL for c in e["result"].get("cell_errors") or [])
                 for e in found.values())
        newest = max((e for e in found.values() if e), key=lambda e: e["date"])
        notebooks.append({"id": nid, "repo": any_e["result"]["repo"], "notebook": any_e["result"]["notebook"],
                          "link": _github_link(newest["repo"], any_e["result"]["notebook"]),
                          "not_applicable_locally": na,
                          **{p: _cell(e) if e else None for p, e in found.items()},
                          "open_issues": open_by_nb.get(nid, [])})

    def rates(rows):
        return {LOCAL: _rate((n[LOCAL], n["not_applicable_locally"]) for n in rows if n[LOCAL]),
                JH: _rate((n[JH], False) for n in rows if n[JH])}

    repos = []
    for name in sorted({n["repo"] for n in notebooks}):
        rows = [n for n in notebooks if n["repo"] == name]
        repos.append({"repo": name, "notebooks": len(rows), **rates(rows)})

    na_ids = {n["id"] for n in notebooks if n["not_applicable_locally"]}
    history = []
    for platform in PLATFORMS:
        by_day = {}
        for e in entries:
            if e["platform"] == platform:
                by_day.setdefault(e["date"], {})[e["id"]] = e  # newest result of a notebook on that day
        for day in sorted(by_day):
            rows = [(_cell(e), platform == LOCAL and nid in na_ids) for nid, e in by_day[day].items()]
            history.append({"date": day, "platform": platform, **_rate(rows)})

    return {"platforms": list(PLATFORMS),
            "latest_run": {p: max((r["started_at"][:10] for r in reports if r["platform"] == p), default=None)
                           for p in PLATFORMS},
            "overall": rates(notebooks), "repos": repos, "notebooks": notebooks, "history": history,
            "labels": _labels(reports),
            "issue_counts": {"open": sum(i["state"] == "open" for i in issues),
                             "closed": sum(i["state"] == "closed" for i in issues)}}


def issues_markdown(issues, labels=None):
    """The open issues as Markdown, one section each, to hand to a person or an LLM."""
    labels = labels or {}
    open_issues = [i for i in issues if i["state"] == "open"]
    lines = ["# Open notebook test issues", "", f"{len(open_issues)} open issue(s).", ""]
    for i in open_issues:
        name = f"[{i['notebook']}]({i['link']})" if i.get("link") else f"`{i['notebook']}`"
        lines += [f"## {i['repo']}: {i['notebook']} ({i['platform']}, {i['id']})", "",
                  f"- Notebook: {name}",
                  f"- Platform: {i['platform']}",
                  f"- Label: {labels.get(i['label'], i['label'])} (`{i['label']}`)",
                  f"- Error: cell {i['cell_number']}, `{i['error_name']}`: {' '.join(i['error_value'].split())}",
                  f"- First seen: {i['first_seen']}, last seen: {i['last_seen']}",
                  f"- Report: `{i['report']}`", ""]
    return "\n".join(lines)


def llms_txt(status):
    """Index of the machine-readable files, in the llms.txt format."""
    c = status["issue_counts"]
    return "\n".join([
        "# Notebook test dashboard", "",
        f"> Results of the weekly notebook tests: {c['open']} open and {c['closed']} closed issue(s). "
        "All files are computed from the same reports as the page.", "",
        "- [status.json](/status.json): pass rates overall and per repository, every notebook with its newest result per platform, history",
        "- [issues.json](/issues.json): all issues with first seen, last seen and closed date",
        "- [issues.md](/issues.md): the open issues, one section each", ""])
