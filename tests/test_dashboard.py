import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from notebook_test.serve import PAGE, Source, make_handler
from notebook_test.status import build_issues, build_status, issues_markdown, llms_txt, load_reports

from test_status import folder, result, write  # noqa: F401  (folder is a fixture)


def test_issues_md_lists_the_open_issues_of_the_page(folder):
    reports = load_reports(folder)
    issues, status = build_issues(reports), build_status(reports)
    md = issues_markdown(issues, status["labels"])
    open_issues = [i for i in issues if i["state"] == "open"]
    assert f"{len(open_issues)} open issue(s)." in md
    assert md.count("\n## ") == len(open_issues)
    for i in open_issues:
        assert i["id"] in md and i["error_name"] in md and i["first_seen"] in md
    assert not any(i["id"] in md for i in issues if i["state"] == "closed")


def test_llms_txt_names_the_data_files(folder):
    text = llms_txt(build_status(load_reports(folder)))
    assert all(f in text for f in ("/status.json", "/issues.json", "/issues.md"))


def request(port, path, method="GET", host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method == "POST" else None)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def commit_all(repo, message):
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


def test_server_serves_page_and_data_and_refreshes_from_a_git_url(folder, tmp_path):
    repo = tmp_path / "results-src"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for p in (folder / "reports").rglob("*.json"):
        target = repo / p.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(p.read_text())
    commit_all(repo, "reports")

    source = Source(str(repo), tmp_path / "cache")
    assert source.refresh() is None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(source))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert request(port, "/")[1] == PAGE.read_text()
        status = json.loads(request(port, "/status.json")[1])
        assert status["overall"]["local"]["total"] == 3 and status["source"] == str(repo)
        assert len(json.loads(request(port, "/issues.json")[1])) == len(build_issues(load_reports(folder)))
        assert "# Open notebook test issues" in request(port, "/issues.md")[1]
        assert request(port, "/llms.txt")[0] == 200 and request(port, "/nope")[0] == 404
        assert request(port, "/status.json", host="evil.example")[0] == 403

        write(repo, "wekeo-jupyterhub", "2026-10-21", "c3", result("a.ipynb"))  # a newer report arrives
        commit_all(repo, "more")
        assert json.loads(request(port, "/status.json")[1])["latest_run"]["wekeo-jupyterhub"] == "2026-10-14"
        assert json.loads(request(port, "/refresh", "POST")[1])["error"] is None
        assert json.loads(request(port, "/status.json")[1])["latest_run"]["wekeo-jupyterhub"] == "2026-10-21"
    finally:
        server.shutdown()
