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

LABEL_TEXT = {
    SERVICE: "service unavailable",
    PACKAGE: "missing package",
    CREDENTIALS: "credentials",
    FOLLOW_ON: "follow-on error",
    TIMEOUT: "timeout",
    KERNEL: "kernel died",
    NOTEBOOK: "notebook error",
}

SERVICE_RE = re.compile(
    r"\b(50[0234])\b|Service Unavailable|Bad Gateway|Gateway Time-?out|ConnectionError|ConnectTimeout|"
    r"ReadTimeout|ClientConnectorError|ServerDisconnectedError|Temporary failure in name resolution|"
    r"Max retries exceeded|Connection (refused|reset|aborted)|RemoteDisconnected", re.I)
CREDENTIALS_RE = re.compile(
    r"\b40[13]\b|Unauthori[sz]ed|Forbidden|invalid (user|credentials|token)|credential|\.hdarc|"
    r"authenticat|StdinNotImplementedError|raw_input was called|getpass", re.I)
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
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def label_error(ename, evalue, traceback_text, failed_names):
    """Return the label for one cell error.

    failed_names: names defined by earlier cells that failed, used to spot follow-on errors.
    """
    text = f"{ename}: {evalue}\n{traceback_text}"
    if ename in ("CellTimeoutError", "TimeoutError") and "Cell execution" in text:
        return TIMEOUT
    if ename == "DeadKernelError":
        return KERNEL
    if ename == "NameError":
        m = NAME_RE.search(evalue or "")
        if m and m.group(1) in failed_names:
            return FOLLOW_ON
    if ename in ("ModuleNotFoundError", "ImportError") or "No module named" in text:
        return PACKAGE
    if SERVICE_RE.search(f"{ename}: {evalue}"):
        return SERVICE
    if CREDENTIALS_RE.search(f"{ename}: {evalue}"):
        return CREDENTIALS
    if ename == "CellTimeoutError":
        return TIMEOUT
    return NOTEBOOK
