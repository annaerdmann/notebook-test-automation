import json

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from wnqa.labels import CREDENTIALS, FOLLOW_ON, NOTEBOOK, PACKAGE, SERVICE, label_error
from wnqa.run import main


def test_labels():
    assert label_error("ClientResponseError", "503, message='Service Unavailable'", "", set()) == SERVICE
    assert label_error("ModuleNotFoundError", "No module named 'hda'", "", set()) == PACKAGE
    assert label_error("HTTPError", "401 Client Error: Unauthorized", "", set()) == CREDENTIALS
    assert label_error("NameError", "name 'options' is not defined", "", {"options"}) == FOLLOW_ON
    assert label_error("NameError", "name 'typo' is not defined", "", {"options"}) == NOTEBOOK
    assert label_error("ZeroDivisionError", "division by zero", "", set()) == NOTEBOOK


def test_run_continues_after_errors_and_labels_them(tmp_path):
    nb = new_notebook(cells=[
        new_markdown_cell("# Test"),
        new_code_cell("a = 1"),
        new_code_cell("import package_that_does_not_exist as pkg"),
        new_code_cell("pkg.load()"),
        new_code_cell("1 / 0"),
        new_code_cell("b = a + 1"),
    ])
    nb.metadata["kernelspec"] = {"name": "python3", "language": "python", "display_name": "Python 3"}
    nbformat.write(nb, tmp_path / "demo.ipynb")
    ok = new_notebook(cells=[new_code_cell("print('hi')")])
    nbformat.write(ok, tmp_path / "ok.ipynb")

    code = main(["--path", str(tmp_path), "--workdir", str(tmp_path / "work"), "--kernel", "python3"])
    assert code == 1

    report = json.loads(next((tmp_path / "work" / "reports").glob("*.json")).read_text())
    results = {r["notebook"]: r for r in report["repos"][0]["results"]}
    assert results["ok.ipynb"]["status"] == "ok"
    demo = results["demo.ipynb"]
    assert [(e["cell_number"], e["label"]) for e in demo["cell_errors"]] == [(3, PACKAGE), (4, FOLLOW_ON), (5, NOTEBOOK)]
    md = next((tmp_path / "work" / "reports").glob("*.md")).read_text()
    assert "1 follow-on error(s) not listed." in md
