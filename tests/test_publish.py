import json

from notebook_test.profiles import load_profile
from notebook_test.publish import catalog_list, dashboard_html, relabel
from notebook_test.report import report_context


def report(profile="wekeo"):
    def err(label, name="E", value="v"):
        return {"cell_number": 3, "code_cell_number": 1, "cell_preview": "x", "error_name": name,
                "error_value": value, "traceback_last_lines": "", "label": label}
    run = {
        "started_at": "2026-10-19T20:00:00", "finished_at": "2026-10-19T21:00:00", "host": "jupyter-test",
        "repos": [{"repo": "wekeo4data", "profile": profile, "url": "https://github.com/wekeo/wekeo4data", "commit": "abc1234",
                   "results": [
                       {"notebook": "ok.ipynb", "status": "ok", "labels": {}, "cell_errors": []},
                       {"notebook": "bug.ipynb", "status": "error", "labels": {"notebook_error": 1, "follow_on": 1},
                        "cell_errors": [err("notebook_error"), err("follow_on")]},
                       {"notebook": "down.ipynb", "status": "error", "labels": {"service_unavailable": 1},
                        "cell_errors": [err("service_unavailable")]},
                   ]}],
    }
    return {**report_context([load_profile(profile)]), **run}


def test_catalog_list_only_blocks_author_problems():
    data = catalog_list(report())
    assert [x["notebook"] for x in data["exclude"]] == ["bug.ipynb"]
    assert data["exclude"][0]["link"] == "https://github.com/wekeo/wekeo4data/blob/abc1234/bug.ipynb"
    assert data["exclude"][0]["reasons"] == ["notebook error"]
    assert "WEkEO JupyterHub" in data["note"]
    assert "WEkEO" not in catalog_list(report("generic"))["note"]


def test_dashboard_renders():
    page = dashboard_html([report()])
    assert "wekeo4data" in page and "33%" in page and "bug.ipynb" in page


def test_relabel_applies_new_rules_and_keeps_follow_on():
    old = report("generic")
    old["repos"][0]["results"][2]["cell_errors"][0].update(
        error_name="HTTPError", error_value="502 Server Error for url: https://x/hda-broker/gettoken", label="service_unavailable")
    new = relabel(old, load_profile("wekeo"))
    res = new["repos"][0]["results"]
    assert res[2]["labels"] == {"credentials": 1}
    assert res[1]["labels"] == {"notebook_error": 1, "follow_on": 1}
    assert new["profile"] == "wekeo" and new["repos"][0]["profile"] == "wekeo"
    json.dumps(new)
