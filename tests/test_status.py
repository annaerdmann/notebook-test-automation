import json
from pathlib import Path

import pytest

from notebook_test.status import build_issues, build_status, load_reports

URL = "https://github.com/wekeo/demo"


def err(label, name="KeyError", value="boom", cell=3):
    return {"cell_number": cell, "code_cell_number": 1, "cell_preview": "x", "error_name": name,
            "error_value": value, "traceback_last_lines": "", "label": label}


def result(notebook, *errors):
    return {"repo": "demo", "notebook": notebook, "status": "error" if errors else "ok", "duration_s": 1,
            "kernel": "python3", "labels": {}, "cell_errors": list(errors)}


def write(folder, platform, day, commit, *results):
    path = Path(folder) / "reports" / platform
    path.mkdir(parents=True, exist_ok=True)
    data = {"started_at": f"{day}T02:00:00", "finished_at": f"{day}T03:00:00", "host": "h",
            "labels": {"notebook_error": {"text": "notebook error"}, "platform": {"text": "needs JupyterHub service"}},
            "repos": [{"repo": "demo", "url": URL, "commit": commit, "results": list(results)}]}
    (path / f"{day}-demo.json").write_text(json.dumps(data))


@pytest.fixture
def folder(tmp_path):
    # a.ipynb: fails, then passes (closed). b.ipynb: passes, then fails (open). c.ipynb needs the JupyterHub.
    write(tmp_path, "local", "2026-10-05", "c1",
          result("a.ipynb", err("notebook_error")), result("b.ipynb"), result("c.ipynb", err("platform", "ValueError")))
    write(tmp_path, "local", "2026-10-12", "c2",
          result("a.ipynb"), result("b.ipynb", err("notebook_error", "ZeroDivisionError")),
          result("c.ipynb", err("platform", "ValueError")))
    write(tmp_path, "wekeo-jupyterhub", "2026-10-14", "c2",
          result("a.ipynb"), result("b.ipynb"), result("c.ipynb"))
    (tmp_path / "notes.json").write_text("{}")  # not a report
    return tmp_path


def test_reports_get_platform_from_folder_and_ids_from_url(folder):
    reports = load_reports(folder)
    assert [r["platform"] for r in reports] == ["local", "local", "wekeo-jupyterhub"]
    assert reports[0]["repos"][0]["results"][0]["notebook_id"] == f"{URL}/a.ipynb"


def test_issues_open_and_closed(folder):
    issues = {i["notebook"]: i for i in build_issues(load_reports(folder)) if i["platform"] == "local"}
    a, b = issues["a.ipynb"], issues["b.ipynb"]
    assert (a["state"], a["first_seen"], a["last_seen"], a["closed_at"]) == ("closed", "2026-10-05", "2026-10-05", "2026-10-12")
    assert a["commits_between"] == {"last_failing": "c1", "closing": "c2"}
    assert (b["state"], b["first_seen"], b["closed_at"], b["error_name"]) == ("open", "2026-10-12", None, "ZeroDivisionError")
    assert len(a["id"]) == 12 and a["link"] == f"{URL}/blob/c1/a.ipynb"


def test_status_numbers_and_jupyterhub_only_notebook(folder):
    status = build_status(load_reports(folder))
    local, jh = status["overall"]["local"], status["overall"]["wekeo-jupyterhub"]
    # newest local run: a passes, b fails, c needs the JupyterHub and is left out of the percentage
    assert (local["passed"], local["total"], local["applicable"], local["pct"]) == (1, 3, 2, 50.0)
    assert (jh["passed"], jh["total"], jh["pct"]) == (3, 3, 100.0)
    nbs = {n["notebook"]: n for n in status["notebooks"]}
    assert nbs["c.ipynb"]["not_applicable_locally"] and not nbs["a.ipynb"]["not_applicable_locally"]
    assert nbs["b.ipynb"]["local"]["date"] == "2026-10-12" and nbs["b.ipynb"]["wekeo-jupyterhub"]["status"] == "ok"
    assert len(nbs["b.ipynb"]["open_issues"]) == 1
    assert status["issue_counts"] == {"open": 2, "closed": 1}  # b (local), c (local: platform error, still open)
    assert status["latest_run"] == {"local": "2026-10-12", "wekeo-jupyterhub": "2026-10-14"}
    assert [(h["date"], h["platform"], h["pct"]) for h in status["history"]] == [
        ("2026-10-05", "local", 50.0), ("2026-10-12", "local", 50.0), ("2026-10-14", "wekeo-jupyterhub", 100.0)]


def test_state_is_computed_only_from_the_reports(folder):
    (folder / "reports" / "wekeo-jupyterhub" / "2026-10-14-demo.json").unlink()
    status = build_status(load_reports(folder))
    assert status["overall"]["wekeo-jupyterhub"]["total"] == 0 and status["latest_run"]["wekeo-jupyterhub"] is None


REAL = Path(__file__).parent.parent.parent / "test-runs" / "2026-10-22-laptop-skill" / "tool-reports" / "baseline.json"


@pytest.mark.skipif(not REAL.exists(), reason="local test runs not available")
def test_real_baseline_report_matches_its_fix_report(tmp_path):
    (tmp_path / "baseline.json").write_text(REAL.read_text())
    status = build_status(load_reports(tmp_path))
    local = status["overall"]["local"]
    assert (local["passed"], local["total"]) == (4, 23)
