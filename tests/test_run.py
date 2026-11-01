import json
import sys

import jsonschema
import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from notebook_test.labels import Labeler
from notebook_test.profiles import ProfileError, load_profile, merge
from notebook_test.report import SCHEMA_VERSION
from notebook_test.run import main
from notebook_test import report as report_module

GENERIC = Labeler(load_profile("generic"))
WEKEO = Labeler(load_profile("wekeo"))
KERNELSPEC = {"name": "python3", "language": "python", "display_name": "Python 3"}


def test_generic_labels():
    L = GENERIC.label_error
    assert L("ClientResponseError", "503, message='Service Unavailable'", "", set()) == "service_unavailable"
    assert L("ModuleNotFoundError", "No module named 'x'", "", set()) == "missing_package"
    assert L("HTTPError", "401 Client Error: Unauthorized", "", set()) == "credentials"
    assert L("NameError", "name 'options' is not defined", "", {"options"}) == "follow_on"
    assert L("NameError", "name 'typo' is not defined", "", {"options"}) == "notebook_error"
    assert L("NameError", "name 'x' is not defined", "", {"*"}) == "follow_on"
    assert L("ZeroDivisionError", "division by zero", "", set()) == "notebook_error"
    assert L("KeyError", "'access_token'", "", set()) == "credentials"
    assert L("DeadKernelError", "died", "", set()) == "kernel_died"
    assert L("CellTimeoutError", "A cell timed out", "", set()) == "timeout"
    assert L("TimeoutError", "Cell execution timed out", "", set()) == "timeout"


def test_generic_has_no_platform_rules():
    assert GENERIC.label_error("ValueError", "No dask-gateway address provided", "", set()) == "notebook_error"


def test_wekeo_platform_labels():
    L = WEKEO.label_error
    assert L("ValueError", "No dask-gateway address provided or found in configuration", "", set()) == "platform"
    assert L("RuntimeError", "Found no NVIDIA driver on your system.", "", set()) == "platform"
    assert L("ConnectError", "[Errno 111] Connection refused", "ollama.chat(model='x')", set()) == "platform"
    assert L("ConnectError", "[Errno 111] Connection refused", "requests.get(url)", set()) == "service_unavailable"
    assert WEKEO.text("platform") == "needs JupyterHub service"


def test_fix_auth_urls_are_credentials_before_service_errors():
    for profile in (WEKEO,):
        assert profile.label_error("HTTPError", "502 Server Error: Bad Gateway for url: https://x/hda-broker/gettoken", "", set()) == "credentials"
        assert profile.label_error("HTTPError", "503 for url: https://x/identity/realms/a", "", set()) == "credentials"
        assert profile.label_error("HTTPError", "400 Client Error: Bad Request for url: https://x/hda-broker/gettoken", "", set()) == "credentials"
    assert GENERIC.label_error("HTTPError", "502 Bad Gateway for url: https://x/oauth/token", "", set()) == "credentials"
    assert GENERIC.label_error("HTTPError", "502 Bad Gateway for url: https://x/data", "", set()) == "service_unavailable"


def test_fix_eocanvas_quota_is_not_a_notebook_error():
    label = WEKEO.label_error("QuotaExceededError", "You have exceeded your quota", "", set())
    assert label == "quota_exceeded"
    assert "quota_exceeded" not in WEKEO.blocking()


def test_profile_merge_and_errors(tmp_path):
    assert merge({"a": [1]}, {"a+": [2]}) == {"a": [1, 2]}
    (tmp_path / "bad.yml").write_text("extends: generic\nclassify:\n  service:\n    - {nolabel: 1}\n")
    with pytest.raises(ProfileError, match="classify"):
        load_profile(str(tmp_path / "bad.yml"))
    (tmp_path / "mine.yml").write_text(
        "extends: generic\nname: mine\nclassify:\n  platform+:\n    - {label: platform, error_regex: 'Foo'}\nprotect_files+: [~/.mine]\n")
    mine = load_profile(str(tmp_path / "mine.yml"))
    assert Labeler(mine).label_error("ValueError", "Foo", "", set()) == "platform"
    assert mine["protect_files"] == ["~/.netrc", "~/.mine"]


def test_python_version_check_message():
    import importlib
    import notebook_test
    real = sys.version_info
    sys.version_info = (3, 9, 6, "final", 0)
    try:
        with pytest.raises(SystemExit, match="Python 3.10 or newer"):
            importlib.reload(notebook_test)
    finally:
        sys.version_info = real
        importlib.reload(notebook_test)


def test_run_continues_after_errors_and_labels_them(tmp_path):
    nb = new_notebook(cells=[
        new_markdown_cell("# Test"),
        new_code_cell("a = 1"),
        new_code_cell("import package_that_does_not_exist as pkg"),
        new_code_cell("pkg.load()"),
        new_code_cell("1 / 0"),
        new_code_cell("b = a + 1"),
    ])
    nb.metadata["kernelspec"] = KERNELSPEC
    nbformat.write(nb, tmp_path / "demo.ipynb")
    nbformat.write(new_notebook(cells=[new_code_cell("print('hi')")]), tmp_path / "ok.ipynb")

    code = main(["--path", str(tmp_path), "--workdir", str(tmp_path / "work"), "--kernel", "python3"])
    assert code == 1

    report = json.loads(next((tmp_path / "work" / "reports").glob("*.json")).read_text())
    jsonschema.validate(report, json.loads((__import__("pathlib").Path(report_module.__file__).parent / "report.schema.json").read_text()))
    assert report["schema_version"] == SCHEMA_VERSION == 1 and report["profile"] == "generic"
    results = {r["notebook"]: r for r in report["repos"][0]["results"]}
    assert results["ok.ipynb"]["status"] == "ok"
    demo = results["demo.ipynb"]
    assert [(e["cell_number"], e["label"]) for e in demo["cell_errors"]] == [(3, "missing_package"), (4, "follow_on"), (5, "notebook_error")]
    md = next((tmp_path / "work" / "reports").glob("*.md")).read_text()
    assert "1 follow-on error(s) not listed." in md and "# Notebook test report" in md


def test_non_wekeo_example_folder_with_generic_profile(tmp_path):
    from pathlib import Path
    examples = Path(__file__).parent.parent / "examples" / "plain-notebooks"
    code = main(["--path", str(examples), "--workdir", str(tmp_path / "work"), "--kernel", "python3"])
    assert code == 1
    report = json.loads(next((tmp_path / "work" / "reports").glob("*.json")).read_text())
    res = {r["notebook"]: r for r in report["repos"][0]["results"]}
    assert res["good/sum.ipynb"]["status"] == "ok"
    assert [e["label"] for e in res["bad/broken.ipynb"]["cell_errors"]] == ["notebook_error"]
    assert "WEkEO" not in json.dumps(report)


def test_protected_files_are_restored_and_come_from_the_profile(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".hdarc").write_text("user: real\npassword: secret\n")
    (home / ".netrc").write_text("machine x\n")
    monkeypatch.setenv("HOME", str(home))
    nb = new_notebook(cells=[new_code_cell(
        "import pathlib\npathlib.Path.home().joinpath('.hdarc').write_text('user: <your_user_name>\\n')\n"
        "pathlib.Path.home().joinpath('.netrc').write_text('x')")])
    nb.metadata["kernelspec"] = KERNELSPEC
    nbformat.write(nb, tmp_path / "save.ipynb")

    main(["--path", str(tmp_path / "save.ipynb"), "--workdir", str(tmp_path / "work"), "--kernel", "python3", "--profile", "wekeo"])
    assert (home / ".hdarc").read_text() == "user: real\npassword: secret\n"
    report = json.loads(next((tmp_path / "work" / "reports").glob("*.json")).read_text())
    assert sorted(report["repos"][0]["results"][0]["changed_credentials"]) == ["~/.hdarc", "~/.netrc"]

    # The generic profile protects only ~/.netrc, so ~/.hdarc is left alone.
    (home / ".netrc").write_text("machine x\n")
    main(["--path", str(tmp_path / "save.ipynb"), "--workdir", str(tmp_path / "work2"), "--kernel", "python3"])
    assert (home / ".hdarc").read_text() == "user: <your_user_name>\n"


def test_reports_are_not_overwritten(tmp_path):
    nb = new_notebook(cells=[new_code_cell("1 + 1")], metadata={"kernelspec": KERNELSPEC})
    nbformat.write(nb, tmp_path / "a.ipynb")
    args = ["--path", str(tmp_path / "a.ipynb"), "--workdir", str(tmp_path / "work"), "--kernel", "python3"]
    main(args)
    main(args)
    main(args + ["--report-name", "rerun"])
    main(args + ["--report-name", "rerun"])
    names = sorted(p.name for p in (tmp_path / "work" / "reports").glob("*.json"))
    assert len(names) == 4
    assert "rerun.json" in names and "rerun_2.json" in names


def test_single_notebook_rerun_reuses_and_updates_the_built_environment(tmp_path, monkeypatch):
    from notebook_test import run as run_module
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    env = repo / "environment.yml"
    env.write_text("dependencies: [python]\n")
    nb = new_notebook(cells=[new_code_cell("1 + 1")], metadata={"kernelspec": KERNELSPEC})
    nbformat.write(nb, repo / "sub" / "a.ipynb")
    builds = []

    def fake_build(env_file, workdir, label):
        builds.append(label)
        return "python3", "built"

    monkeypatch.setattr(run_module, "build_env", fake_build)
    work = str(tmp_path / "work")
    cfg = tmp_path / "config.yml"
    cfg.write_text(f"workdir: {work}\nrepos:\n  - path: {repo}\n    name: repo\n    environments: build\n")
    run_module.main([str(cfg)])
    assert builds == ["repo"]
    # Rerun one notebook with --path: same environment, nothing rebuilt.
    run_module.main(["--path", str(repo / "sub" / "a.ipynb"), "--workdir", work])
    assert builds == ["repo"]
    # The environment file changed (an allowed fix): the rerun updates the same environment.
    env.write_text("dependencies: [python, numpy]\n")
    run_module.main(["--path", str(repo / "sub" / "a.ipynb"), "--workdir", work])
    assert builds == ["repo", "repo"]
    reports = sorted((tmp_path / "work" / "reports").glob("*.json"))
    last = json.loads(reports[-1].read_text())
    assert last["repos"][0]["results"][0]["kernel"] == "python3"


def test_report_has_platform_tool_version_and_notebook_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_API_URL", raising=False)
    (tmp_path / "nbs").mkdir()
    nbformat.write(new_notebook(cells=[new_code_cell("1")]), tmp_path / "nbs" / "ok.ipynb")
    (tmp_path / "nbs" / "environment.yml").write_text("name: x\n")
    assert main(["--path", str(tmp_path / "nbs"), "--workdir", str(tmp_path / "w"), "--kernel", "python3"]) == 0
    report = json.loads(next((tmp_path / "w" / "reports").glob("*.json")).read_text())
    jsonschema.validate(report, json.loads((__import__("pathlib").Path(report_module.__file__).parent / "report.schema.json").read_text()))
    assert report["platform"] == "local" and report["tool_version"]
    res = report["repos"][0]["results"][0]
    assert res["notebook_id"] == "nbs/ok.ipynb"
    assert res["env_file"] == "environment.yml" and len(res["env_sha256"]) == 64
    assert res["env_source"] == "fallback"  # an environment file exists but nothing was built


def test_platform_comes_from_config_or_jupyterhub_variable(monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_API_URL", raising=False)
    assert report_module.detect_platform() == "local"
    monkeypatch.setenv("JUPYTERHUB_API_URL", "http://hub/api")
    assert report_module.detect_platform() == "wekeo-jupyterhub"
    assert report_module.detect_platform("local") == "local"


def test_notebook_id_normalises_the_repository_url():
    f = report_module.notebook_id
    assert f("https://GitHub.com/WEkEO/wekeo-hda.git", "x", "a/b.ipynb") == "https://github.com/wekeo/wekeo-hda/a/b.ipynb"
    assert f("git@github.com:wekeo/wekeo-hda.git", "x", "b.ipynb") == "https://github.com/wekeo/wekeo-hda/b.ipynb"
    assert f(None, "demo", "b.ipynb") == "demo/b.ipynb"
