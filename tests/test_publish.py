from wnqa.publish import catalog_list, dashboard_html


def report():
    def err(label):
        return {"cell_number": 3, "code_cell_number": 1, "cell_preview": "x", "error_name": "E",
                "error_value": "v", "traceback_last_lines": "", "label": label}
    return {
        "started_at": "2026-10-19T20:00:00", "finished_at": "2026-10-19T21:00:00", "host": "jupyter-test",
        "repos": [{"repo": "wekeo4data", "url": "https://github.com/wekeo/wekeo4data", "commit": "abc1234",
                   "results": [
                       {"notebook": "ok.ipynb", "status": "ok", "labels": {}, "cell_errors": []},
                       {"notebook": "bug.ipynb", "status": "error", "labels": {"notebook_error": 1, "follow_on": 1},
                        "cell_errors": [err("notebook_error"), err("follow_on")]},
                       {"notebook": "down.ipynb", "status": "error", "labels": {"service_unavailable": 1},
                        "cell_errors": [err("service_unavailable")]},
                   ]}],
    }


def test_catalog_list_only_blocks_author_problems():
    data = catalog_list(report())
    assert [x["notebook"] for x in data["exclude"]] == ["bug.ipynb"]
    assert data["exclude"][0]["link"] == "https://github.com/wekeo/wekeo4data/blob/abc1234/bug.ipynb"


def test_dashboard_renders():
    page = dashboard_html([report()])
    assert "wekeo4data" in page and "33%" in page and "bug.ipynb" in page
