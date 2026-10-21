"""Build one conda environment per `environment.yml` and register it as a Jupyter kernel.

Port of the DEx team's `env_create.txt` (wekeo-dex): it walked the submodules, created or updated an
environment from each environment file, installed ipykernel and registered a kernel. Here the
environments live under the work folder, so removing the work folder removes everything.
"""

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


def env_files(root):
    """environment.yml/yaml files in the repo, nearest first per folder (skips hidden folders)."""
    found = []
    for name in ("environment.yml", "environment.yaml"):
        for p in Path(root).rglob(name):
            if not any(part.startswith(".") for part in p.relative_to(root).parts[:-1]):
                found.append(p)
    return sorted(found)


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower()


def build_env(env_file, workdir, label):
    """Create (or update) the environment and register a kernel. Returns (kernel name, log)."""
    tool, exe = find_solver()
    if not exe:
        return None, "No micromamba, mamba or conda found; cannot build environments."
    prefix = Path(workdir) / "envs" / _safe(label)
    kernel = f"wnqa-{_safe(label)}"
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
    # Make the kernel visible to this process (JUPYTER_PATH is read on each kernelspec lookup).
    data_dir = str(kernels_dir / "share" / "jupyter")
    paths = os.environ.get("JUPYTER_PATH", "").split(os.pathsep) if os.environ.get("JUPYTER_PATH") else []
    if data_dir not in paths:
        os.environ["JUPYTER_PATH"] = os.pathsep.join([data_dir] + paths)
    return kernel, "\n".join(log)


def kernel_for(nb_path, kernels_by_folder):
    """The kernel built for the nearest folder above the notebook that has an environment file."""
    folder = Path(nb_path).parent.resolve()
    best, best_len = None, -1
    for env_folder, kernel in kernels_by_folder.items():
        env_folder = Path(env_folder).resolve()
        if kernel and (folder == env_folder or env_folder in folder.parents) and len(env_folder.parts) > best_len:
            best, best_len = kernel, len(env_folder.parts)
    return best
