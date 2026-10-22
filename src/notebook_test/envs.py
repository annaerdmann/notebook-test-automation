"""Build one conda environment per `environment.yml` and register it as a Jupyter kernel.

Port of the DEx team's `env_create.txt` (wekeo-dex): it walked the submodules, created or updated an
environment from each environment file, installed ipykernel and registered a kernel. Here the
environments live under the work folder, so removing the work folder removes everything.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def find_solver():
    for tool in ("micromamba", "mamba", "conda"):
        path = shutil.which(tool)
        if path:
            return tool, path
    return None, None


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower()


def build_env(env_file, workdir, label):
    """Create (or update) the environment and register a kernel. Returns (kernel name, log)."""
    tool, exe = find_solver()
    if not exe:
        return None, "No micromamba, mamba or conda found; cannot build environments."
    prefix = Path(workdir) / "envs" / _safe(label)
    kernel = f"notebook-test-{_safe(label)}"
    env = dict(os.environ)
    if tool == "micromamba":
        env.setdefault("MAMBA_ROOT_PREFIX", str(Path(workdir) / "micromamba"))
    action = "update" if (prefix / "conda-meta").is_dir() else "create"
    if tool == "micromamba":
        cmd = [exe, action, "-y", "-p", str(prefix), "-f", str(env_file)]
    else:
        cmd = [exe, "env", action, "-p", str(prefix), "-f", str(env_file)] + (["--prune"] if action == "update" else [])
    log = [" ".join(cmd)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    log.append(r.stdout[-3000:] + r.stderr[-3000:])
    if r.returncode != 0:
        return None, "\n".join(log)
    python = prefix / "bin" / "python"
    if subprocess.run([str(python), "-c", "import ipykernel"], capture_output=True).returncode != 0:
        inst = [exe, "install", "-y", "-p", str(prefix), "-c", "conda-forge", "ipykernel"]
        r = subprocess.run(inst, capture_output=True, text=True, env=env)
        log.append(" ".join(inst) + "\n" + r.stderr[-2000:])
        if r.returncode != 0:
            return None, "\n".join(log)
    kernels_dir = Path(workdir) / "jupyter"
    r = subprocess.run([str(python), "-m", "ipykernel", "install", "--prefix", str(kernels_dir),
                        "--name", kernel, "--display-name", f"Python ({label})"], capture_output=True, text=True)
    log.append(r.stdout + r.stderr)
    if r.returncode != 0:
        return None, "\n".join(log)
    use_kernels(workdir)
    return kernel, "\n".join(log)


def use_kernels(workdir):
    """Make kernels built in this work folder visible to this process (JUPYTER_PATH is read on each
    kernelspec lookup)."""
    data_dir = str(Path(workdir) / "jupyter" / "share" / "jupyter")
    paths = os.environ.get("JUPYTER_PATH", "").split(os.pathsep) if os.environ.get("JUPYTER_PATH") else []
    if data_dir not in paths:
        os.environ["JUPYTER_PATH"] = os.pathsep.join([data_dir] + paths)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _registry(workdir):
    return Path(workdir) / "envs" / "kernels.json"


def load_built(workdir):
    """Environments built earlier in this work folder: {environment file: {kernel, label, sha256}}."""
    try:
        return json.loads(_registry(workdir).read_text())
    except (OSError, ValueError):
        return {}


def save_built(workdir, env_file, kernel, label):
    built = load_built(workdir)
    built[str(Path(env_file).resolve())] = {"kernel": kernel, "label": label, "sha256": file_hash(env_file)}
    _registry(workdir).parent.mkdir(parents=True, exist_ok=True)
    _registry(workdir).write_text(json.dumps(built, indent=2))


def nearest_env_file(nb_path, root):
    """The environment file in the notebook's folder or the nearest folder above it, up to `root`."""
    root = Path(root).resolve()
    folder = Path(nb_path).resolve().parent
    while True:
        for name in ("environment.yml", "environment.yaml"):
            if (folder / name).is_file():
                return folder / name
        if folder == root or root not in folder.parents:
            return None
        folder = folder.parent

