"""Label cell errors so the report separates notebook bugs from setup and service problems."""

import ast
import re

SERVICE = "service_unavailable"
PACKAGE = "missing_package"
CREDENTIALS = "credentials"
FOLLOW_ON = "follow_on"
TIMEOUT = "timeout"
KERNEL = "kernel_died"
NOTEBOOK = "notebook_error"
PLATFORM = "platform"

LABEL_TEXT = {
    SERVICE: "service unavailable",
    PACKAGE: "missing package",
    CREDENTIALS: "credentials",
    FOLLOW_ON: "follow-on error",
    TIMEOUT: "timeout",
    KERNEL: "kernel died",
    NOTEBOOK: "notebook error",
    PLATFORM: "needs JupyterHub service",
}

SERVICE_RE = re.compile(
    r"\b(50[0234])\b|Service Unavailable|Bad Gateway|Gateway Time-?out|ConnectionError|ConnectTimeout|"
    r"ReadTimeout|ClientConnectorError|ServerDisconnectedError|Temporary failure in name resolution|"
    r"Max retries exceeded|Connection (refused|reset|aborted)|RemoteDisconnected", re.I)
CREDENTIALS_RE = re.compile(
    r"\b40[13]\b|Unauthori[sz]ed|Forbidden|invalid (user|credentials|token)|credential|\.hdarc|"
    r"authenticat|StdinNotImplementedError|raw_input was called|getpass|access_token|gettoken", re.I)
# Services the WEkEO JupyterHub provides but a VM or laptop usually lacks.
PLATFORM_RE = re.compile(
    r"dask-gateway address|NVIDIA driver|CUDA (driver|is not available)|no CUDA GPUs|"
    r"localhost:11434|127\.0\.0\.1:11434", re.I)
NAME_RE = re.compile(r"name '([^']+)' is not defined")


def defined_names(source):
    """Names a cell would define: assignments, imports, functions and classes."""
    names = set()
    try:
        tree = ast.parse("\n".join(l for l in source.splitlines() if not l.lstrip().startswith(("%", "!"))))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                # "*" marks a star import: any later NameError may be a follow-on error.
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def label_error(ename, evalue, traceback_text, failed_names):
    """Return the label for one cell error.

    traceback_text: the full traceback, plus the cell source when available.
    failed_names: names defined by earlier cells that failed, used to spot follow-on errors.
    """
    text = f"{ename}: {evalue}\n{traceback_text}"
    if ename in ("CellTimeoutError", "TimeoutError") and "Cell execution" in text:
        return TIMEOUT
    if ename == "DeadKernelError":
        return KERNEL
    if ename == "NameError":
        m = NAME_RE.search(evalue or "")
        if m and (m.group(1) in failed_names or "*" in failed_names):
            return FOLLOW_ON
    if ename in ("ModuleNotFoundError", "ImportError") or "No module named" in text:
        return PACKAGE
    if PLATFORM_RE.search(text) or ("Connection refused" in (evalue or "") and "ollama" in text.lower()):
        return PLATFORM
    if SERVICE_RE.search(f"{ename}: {evalue}"):
        return SERVICE
    if CREDENTIALS_RE.search(f"{ename}: {evalue}"):
        return CREDENTIALS
    if ename == "CellTimeoutError":
        return TIMEOUT
    return NOTEBOOK
