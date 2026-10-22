"""Load profiles: YAML files that say how test results are labelled and reported (copied from notebook-qa-automation).

A profile is a bundled name (`generic`, `wekeo`, ...) or a path to a YAML file. It can `extends:`
another profile (name or path relative to the file). Merge rules: mappings merge key by key, any other
value of the child replaces the parent's, and a key ending in `+` appends its list to the parent's list
(`required+: [x]`).
"""

import copy
import json
from importlib import resources
from pathlib import Path

import yaml

try:
    import jsonschema
except ImportError:  # validation is skipped when jsonschema is missing
    jsonschema = None

BUNDLED = "profiles"


class ProfileError(Exception):
    pass


def _bundled_names():
    return sorted(p.name[:-4] for p in resources.files("notebook_test").joinpath(BUNDLED).iterdir() if p.name.endswith(".yml"))


def merge(parent, child):
    out = copy.deepcopy(parent)
    for key, value in child.items():
        if key.endswith("+"):
            base = key[:-1]
            out[base] = list(out.get(base) or []) + list(value or [])
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read(ref, base_dir=None, seen=()):
    ref = str(ref)
    if ref in _bundled_names() and not ref.endswith(".yml"):
        text = resources.files("notebook_test").joinpath(BUNDLED, ref + ".yml").read_text()
        origin, folder = f"bundled profile `{ref}`", None
    else:
        path = Path(ref).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = Path(base_dir) / path
        if not path.is_file():
            raise ProfileError(f"Profile `{ref}` not found. Bundled profiles: {', '.join(_bundled_names())}; "
                               "or give the path of a YAML file.")
        text, origin, folder = path.read_text(), str(path), path.parent
    if origin in seen:
        raise ProfileError(f"Profile `extends` loop at {origin}.")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"{origin} is not valid YAML: {e}") from e
    parent = data.pop("extends", None)
    if parent:
        data = merge(_read(parent, folder, seen + (origin,)), data)
    return data


def schema():
    return json.loads(resources.files("notebook_test").joinpath(BUNDLED, "profile.schema.json").read_text())


def validate(data):
    if jsonschema is None:
        return
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = ".".join(str(p) for p in e.path) or "(top level)"
        raise ProfileError(f"Invalid profile at {where}: {e.message}")


def load_profile(ref="generic", overrides=None, base_dir=None):
    data = _read(ref, base_dir)
    if overrides:
        data = merge(data, overrides)
    validate(data)
    return data
