"""`notebook-test run`: execute notebooks and write a test report.

Based on the DEx team's AutoChecksV3.py (wekeo-dex, MIT): every cell runs with nbclient and the
run continues after errors. Added: a config file for several repositories, profile-driven error labels,
follow-on detection, one rerun when a service was unavailable, and a combined report.

Usage:
    notebook-test run CONFIG.yml [--workdir DIR] [--only REPO_NAME] [--profile NAME_OR_FILE]
    notebook-test run --path FOLDER_OR_NOTEBOOK [--kernel NAME] [--workdir DIR] [--profile NAME_OR_FILE]
    Both take --report-name NAME. Reports are never overwritten: a second run on the same day gets
    the start time added to its file name.

Rerunning single notebooks: environments built in a work folder are remembered there
(envs/kernels.json). A later run with the same --workdir, also with --path on one notebook, uses the
environment of the nearest environment file above the notebook again, and updates it first if that
file has changed since.

Config file:
    profile: generic              # bundled profile name or YAML file (labels, protected files, report texts)
    profile_overrides: {}         # merged over the profile
    workdir: ./notebook-test-work # clones, executed notebooks and reports go here
    timeout: 1200                 # seconds per cell
    platform: local               # "local" or "wekeo-jupyterhub"; default: the JupyterHub if JUPYTERHUB_API_URL is set
    kernel: python3               # default kernel; "auto" uses the notebook's own kernelspec
    rerun_wait: 60                # seconds to wait before rerunning after a service error
    repos:
      - url: https://github.com/example/notebooks
        ref: main                 # optional
        kernel: auto              # optional, overrides the default
        profile: wekeo            # optional, overrides the profile for this repository
        skip: ["**/draft_*.ipynb"]  # optional globs, relative to the repo
        environments: build       # optional: build a conda env per environment.yml (needs micromamba,
                                  # mamba or conda) and run the notebooks below it in that env
                                  # (only for folders with notebooks in scope)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import nbformat
import yaml
from jupyter_client.kernelspec import KernelSpecManager
from nbclient import NotebookClient

from .envs import build_env, file_hash, load_built, nearest_env_file, save_built, use_kernels
from .labels import KERNEL, Labeler, defined_names
from .profiles import ProfileError, load_profile
from .report import detect_platform, notebook_id, tool_version, write_reports


@dataclass
class CellError:
    cell_number: int
    code_cell_number: int
    cell_preview: str
    error_name: str
    error_value: str
    traceback_last_lines: str
    label: str = "notebook_error"


@dataclass
class NotebookRunResult:
    repo: str
    notebook: str           # path relative to the repo
    status: str             # "ok" or "error"
    duration_s: float
    kernel: str
    executed_path: str | None = None
    errored_path: str | None = None
    error_message: str | None = None
    reran: bool = False
    labels: dict = field(default_factory=dict)  # label -> count
    cell_errors: list = field(default_factory=list)
    changed_credentials: list = field(default_factory=list)  # credential files the notebook overwrote (restored)
    notebook_id: str = ""   # <repo url>/<notebook path>, the same on every platform
    env_source: str = "platform-kernel"  # "built", "platform-kernel" or "fallback" (environment file but no kernel)
    env_file: str | None = None  # environment file in the repo that applies to the notebook
    env_sha256: str | None = None


def _preview(cell, max_len=70):
    for line in cell.source.splitlines():
        s = line.strip()
        if s:
            return (s[:max_len] + "…") if len(s) > max_len else s
    return "[empty code cell]"


def _strip_ansi(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _installed_kernels():
    try:
        return set(KernelSpecManager().find_kernel_specs())
    except Exception:
        return set()


def choose_kernel(nb, wanted, installed):
    if wanted and wanted != "auto":
        return wanted
    name = (nb.metadata.get("kernelspec") or {}).get("name")
    if name and name in installed:
        return name
    lang = ((nb.metadata.get("kernelspec") or {}).get("language") or "python").lower()
    if lang == "r" and "ir" in installed:
        return "ir"
    return "python3"


def execute(nb_path, kernel, timeout, out_dir, labeler):
    """Run one notebook top to bottom, keep going after errors, label every error."""
    nb = nbformat.read(nb_path, as_version=4)
    client = NotebookClient(nb, timeout=timeout, kernel_name=kernel,
                            resources={"metadata": {"path": str(nb_path.parent)}}, allow_errors=True)
    errors, failed_names = [], set()
    start = time.perf_counter()
    code_index = 0
    try:
        with client.setup_kernel():
            for i, cell in enumerate(nb.cells):
                if cell.cell_type != "code":
                    continue
                code_index += 1
                cell_errs = []
                try:
                    client.execute_cell(cell, i)
                    for out in cell.get("outputs", []):
                        if out.get("output_type") == "error":
                            tb = _strip_ansi("\n".join(out.get("traceback", [])))
                            cell_errs.append((out.get("ename", ""), out.get("evalue", ""), tb))
                except Exception as e:  # timeout, dead kernel
                    cell_errs.append((type(e).__name__, str(e)[:500], str(e)[-2000:]))
                    if type(e).__name__ == "DeadKernelError":
                        errors.append(CellError(i + 1, code_index, _preview(cell), *cell_errs[-1], label=KERNEL))
                        break
                for ename, evalue, tb in cell_errs:
                    label = labeler.label_error(ename, evalue, tb + "\n" + cell.source, failed_names)
                    errors.append(CellError(i + 1, code_index, _preview(cell), ename, evalue,
                                            "\n".join(tb.splitlines()[-20:]), label))
                if cell_errs:
                    failed_names |= defined_names(cell.source)
    except Exception as e:  # kernel could not start
        errors.append(CellError(0, 0, "[kernel start]", type(e).__name__, str(e)[:500], str(e)[-2000:], KERNEL))
    duration = time.perf_counter() - start
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".errored.ipynb" if errors else ".executed.ipynb"
    out_path = out_dir / (nb_path.stem + suffix)
    nbformat.write(nb, out_path)
    return errors, duration, out_path


def run_notebook(repo_name, repo_root, nb_path, kernel, timeout, out_dir, rerun_wait, labeler, protect_files,
                 repo_url=None, env=None):
    rel = os.path.relpath(nb_path, repo_root)
    print(f"▶ {repo_name}/{rel} (kernel {kernel})", flush=True)
    snap, changed = snapshot_credentials(protect_files), set()

    def attempt():
        try:
            return execute(nb_path, kernel, timeout, out_dir, labeler)
        finally:
            for f in restore_credentials(snap):
                print(f"  notebook changed {f}; restored", flush=True)
                changed.add(f)

    errors, duration, out_path = attempt()
    reran = False
    if any(e.label in labeler.profile.get("rerun_on", []) for e in errors):
        print(f"  service unavailable, rerunning after {rerun_wait}s", flush=True)
        time.sleep(rerun_wait)
        errors, duration, out_path = attempt()
        reran = True
    labels = {}
    for e in errors:
        labels[e.label] = labels.get(e.label, 0) + 1
    status = "error" if errors else "ok"
    summary = ", ".join(f"{n} {labeler.text(l)}" for l, n in labels.items()) or "ok"
    print(f"  {'✔' if status == 'ok' else '✖'} {duration:.0f}s – {summary}", flush=True)
    return NotebookRunResult(
        repo=repo_name, notebook=rel, status=status, duration_s=round(duration, 1), kernel=kernel,
        executed_path=str(out_path) if status == "ok" else None,
        errored_path=str(out_path) if status == "error" else None,
        error_message=f"{len(errors)} cell error(s)" if errors else None,
        reran=reran, labels=labels, cell_errors=[asdict(e) for e in errors],
        changed_credentials=sorted(changed), notebook_id=notebook_id(repo_url, repo_name, rel),
        **(env or {}),
    )


def git_root(path):
    """The repository a notebook file belongs to (nearest folder with .git), else its own folder."""
    path = Path(path).resolve()
    for folder in path.parents:
        if (folder / ".git").exists():
            return folder
    return path.parent


def find_notebooks(root, skip=()):
    out = []
    for p in sorted(root.rglob("*.ipynb")):
        rel = os.path.relpath(p, root)
        parts = Path(rel).parts
        if ".ipynb_checkpoints" in parts or any(x.startswith(".") for x in parts):
            continue
        if p.name.endswith((".executed.ipynb", ".errored.ipynb")):
            continue
        if any(fnmatch(rel, g) for g in skip):
            continue
        out.append(p)
    return out


def clone(url, ref, dest):
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "1", "origin", ref or "HEAD"], check=True)
        subprocess.run(["git", "-C", str(dest), "reset", "--hard", "FETCH_HEAD"], check=True)
    else:
        cmd = ["git", "clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules"]
        if ref:
            cmd += ["--branch", ref]
        subprocess.run(cmd + [url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "submodule", "update", "--init", "--recursive", "--depth", "1"], check=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return head


def snapshot_credentials(files):
    """Remember the profile's `protect_files`: notebooks may overwrite them, e.g. with placeholder credentials."""
    snap = {}
    for f in files:
        p = Path(f).expanduser()
        snap[p] = p.read_bytes() if p.is_file() else None
    return snap


def restore_credentials(snap):
    """Put credential files back as they were. Returns the files a notebook had changed."""
    changed = []
    for p, data in snap.items():
        now = p.read_bytes() if p.is_file() else None
        if now == data:
            continue
        changed.append(str(p).replace(str(Path.home()), "~"))
        if data is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    return changed


def repo_skips(root):
    """`skip_notebooks` globs of the repository's own .github/notebook-qa.yml (shared with notebook-qa)."""
    path = Path(root) / ".github" / "notebook-qa.yml"
    if root.is_dir() and path.is_file():
        return list((yaml.safe_load(path.read_text()) or {}).get("skip_notebooks") or [])
    return []


def profile_for(entry, defaults):
    ref = entry.get("profile") or defaults.get("profile") or "generic"
    return load_profile(ref, None if entry.get("profile") else defaults.get("profile_overrides"))


def run_repo(entry, defaults, workdir, profile):
    url = entry.get("url")
    path = entry.get("path")
    name = entry.get("name") or Path(url or path).name.removesuffix(".git")
    if url:
        root = workdir / "repos" / name
        root.parent.mkdir(parents=True, exist_ok=True)
        commit = clone(url, entry.get("ref"), root)
    else:
        root = Path(path).resolve()
        commit = None
    timeout = int(entry.get("timeout", defaults.get("timeout", 1200)))
    wanted = entry.get("kernel", defaults.get("kernel", "auto"))
    rerun_wait = int(defaults.get("rerun_wait", 60))
    labeler = Labeler(profile)
    notebooks = find_notebooks(root, list(entry.get("skip", [])) + repo_skips(root)) if root.is_dir() else [root]
    build = entry.get("environments", defaults.get("environments")) == "build"
    platform = detect_platform(defaults.get("platform"))
    repo_root = root if root.is_dir() else git_root(root)
    built = load_built(workdir)
    if built:
        use_kernels(workdir)
    env_logs, kernel_by_env = {}, {}

    def env_kernel(env_file):
        """Kernel for an environment file. An environment built earlier in this work folder is reused
        while the file is unchanged, and updated when the file changed. New ones are built only with
        `environments: build`."""
        key = str(env_file.resolve())
        if key in kernel_by_env:
            return kernel_by_env[key]
        rec = built.get(key)
        kernel = None
        if rec and rec.get("sha256") == file_hash(env_file):
            kernel = rec["kernel"]
        elif rec or build:
            rel = os.path.relpath(env_file, repo_root)
            label = (rec or {}).get("label") or f"{name}-{os.path.relpath(env_file.parent, repo_root)}".rstrip("-.")
            print(f"  {'updating' if rec else 'building'} environment {rel}", flush=True)
            kernel, log = build_env(env_file, workdir, label)
            env_logs[rel] = {"kernel": kernel, "ok": bool(kernel), "log": log[-4000:]}
            if kernel:
                save_built(workdir, env_file, kernel, label)
                built[key] = {"kernel": kernel, "label": label}
            else:
                print("  environment build failed; notebooks there use the default kernel", flush=True)
        kernel_by_env[key] = kernel
        return kernel

    installed = _installed_kernels()
    if not root.is_dir():
        root = root.parent
    print(f"\n=== {name}: {len(notebooks)} notebook(s) ===", flush=True)
    results = []
    for nb_path in notebooks:
        nb = nbformat.read(nb_path, as_version=4)
        lang = ((nb.metadata.get("kernelspec") or {}).get("language") or "python").lower()
        env_file = nearest_env_file(nb_path, repo_root) if lang == "python" else None
        own = env_kernel(env_file) if env_file else None
        if own and own not in installed:
            installed = _installed_kernels()
        kernel = own or choose_kernel(nb, wanted, installed)
        env = {"env_source": "built" if own else ("fallback" if env_file and platform == "local" else "platform-kernel"),
               "env_file": os.path.relpath(env_file, repo_root) if env_file else None,
               "env_sha256": file_hash(env_file) if env_file else None}
        out_dir = workdir / "outputs" / name / Path(os.path.relpath(nb_path.parent, root))
        results.append(run_notebook(name, root, nb_path, kernel, timeout, out_dir, rerun_wait, labeler,
                                    profile.get("protect_files", []), url, env))
    return {"repo": name, "profile": profile["name"], "url": url, "commit": commit, "environments": env_logs, "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="notebook-test run", description="Execute notebooks and write a test report.")
    ap.add_argument("config", nargs="?", help="config YAML (see module docstring)")
    ap.add_argument("--path", help="run notebooks in this local folder or file instead of a config")
    ap.add_argument("--kernel", default=None, help="kernel name, or 'auto' (default)")
    ap.add_argument("--workdir", default=None, help="work folder (default from config, else ./notebook-test-work)")
    ap.add_argument("--profile", default=None, help="bundled profile name or YAML file (overrides the config)")
    ap.add_argument("--only", action="append", help="only run the repo with this name (repeatable)")
    ap.add_argument("--report-name", default=None,
                    help="file name (without extension) for the report; a number is added if it exists")
    a = ap.parse_args(argv)

    if a.config:
        cfg = yaml.safe_load(Path(a.config).read_text()) or {}
    elif a.path:
        cfg = {"repos": [{"path": a.path}]}
    else:
        ap.error("give a config file or --path")
    if a.kernel:
        cfg["kernel"] = a.kernel
    if a.profile:
        cfg["profile"] = a.profile
    workdir = Path(a.workdir or cfg.get("workdir") or "notebook-test-work").expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        default_profile = load_profile(cfg.get("profile") or "generic", cfg.get("profile_overrides"))
        profiles = {id(e): profile_for(e, cfg) for e in cfg.get("repos", [])}
    except ProfileError as e:
        print(f"notebook-test: {e}", file=sys.stderr)
        return 2
    repos = []
    for entry in cfg.get("repos", []):
        name = entry.get("name") or Path(entry.get("url") or entry.get("path")).name.removesuffix(".git")
        if a.only and name not in a.only:
            continue
        repos.append(run_repo(entry, cfg, workdir, profiles[id(entry)]))

    run = {"platform": detect_platform(cfg.get("platform")), "tool_version": tool_version(),
           "started_at": started, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "host": os.uname().nodename, "repos": repos}
    paths = write_reports(run, workdir / "reports", [default_profile] + list(profiles.values()), a.report_name)
    print(f"\nReports: {', '.join(str(p) for p in paths)}")
    failed = sum(r.status == "error" for repo in repos for r in repo["results"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
