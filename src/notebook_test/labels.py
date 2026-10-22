"""Label cell errors so the report separates notebook bugs from setup and service problems.

The labels and the rules that assign them come from the profile (see profiles/generic.yml).
"""

import ast
import re

FOLLOW_ON = "follow_on"
KERNEL = "kernel_died"
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


class Labeler:
    def __init__(self, profile):
        self.profile = profile
        self.labels = profile["labels"]
        self.default = profile.get("default_label", "notebook_error")
        self.rules = []
        stages = profile.get("classify") or {}
        for stage in profile.get("classify_order") or list(stages):
            for rule in stages.get(stage) or []:
                self.rules.append(self._compile(rule))

    @staticmethod
    def _compile(rule):
        def rx(key):
            return re.compile(rule[key]) if key in rule else None
        return {"label": rule["label"], "ename": set(rule.get("ename") or []), "follow_on": rule.get("follow_on", False),
                "ename_regex": rx("ename_regex"), "evalue_regex": rx("evalue_regex"),
                "error_regex": rx("error_regex"), "text_regex": rx("text_regex")}

    def text(self, label):
        return self.labels.get(label, {}).get("text", label)

    def blocking(self):
        return {k for k, v in self.labels.items() if v.get("blocking")}

    def label_error(self, ename, evalue, traceback_text, failed_names):
        """Return the label for one cell error.

        traceback_text: the full traceback, plus the cell source when available.
        failed_names: names defined by earlier cells that failed, used to spot follow-on errors.
        """
        evalue = evalue or ""
        text = f"{ename}: {evalue}\n{traceback_text}"
        error = f"{ename}: {evalue}"
        for r in self.rules:
            if r["ename"] and ename not in r["ename"]:
                continue
            if r["follow_on"]:
                m = ename == "NameError" and NAME_RE.search(evalue)
                if not (m and (m.group(1) in failed_names or "*" in failed_names)):
                    continue
            if r["ename_regex"] and not r["ename_regex"].search(ename or ""):
                continue
            if r["evalue_regex"] and not r["evalue_regex"].search(evalue):
                continue
            if r["error_regex"] and not r["error_regex"].search(error):
                continue
            if r["text_regex"] and not r["text_regex"].search(text):
                continue
            return r["label"]
        return self.default
