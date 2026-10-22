# notebook-test-automation

Runs every cell of every Jupyter notebook, keeps going after errors, labels each error and writes a JSON and Markdown report, a dashboard page and an exclude list. Labels, protected credential files, exclude rules and report texts come from **profiles** (YAML), so the tool works on any notebook folder with the `generic` profile. WEkEO users: use the `wekeo` profile (see [WEkEO](#wekeo)).

The tool does not change notebooks and does not open issues. The sister tool [notebook-qa-automation](https://github.com/annaerdmann/notebook-qa-automation) runs static checks.

## Install

```bash
pip install git+https://github.com/annaerdmann/notebook-test-automation
```

**Needs Python 3.10 or newer** (`requires-python = ">=3.10"`). On older Python, `pip` refuses to install and the command stops with the message "needs Python 3.10 or newer". macOS ships Python 3.9, so use Homebrew or another interpreter. Building environments (`environments: build`) also needs micromamba, mamba or conda.

## Run

```bash
notebook-test run --path some/folder          # local notebooks, generic profile
notebook-test run --path some/folder --profile wekeo
notebook-test run config.yml                  # several repositories
```

Example `config.yml`:

```yaml
profile: generic          # bundled name or YAML file; default generic
profile_overrides: {}     # merged over the profile
workdir: ~/notebook-test-work   # clones, environments, executed notebooks and reports
timeout: 1200             # seconds per cell
kernel: auto              # notebook's own kernel if installed, else python3 (ir for R)
rerun_wait: 60            # wait before rerunning a notebook after a service error
repos:
  - url: https://github.com/example/notebooks
  - url: https://github.com/example/more-notebooks
    environments: build   # build a conda env per environment.yml and use it
    skip: ["**/old/**"]
    profile: wekeo        # optional, per repository
```

A repository's own `skip_notebooks` globs in `.github/notebook-qa.yml` (shared with notebook-qa) are honoured too. Submodules are cloned. Reports go to `<workdir>/reports/`, executed notebooks to `<workdir>/outputs/`. Executed notebooks can contain outputs from your account, so do not publish them. Remove the work folder to clean up everything, environments included.

Reports are never overwritten: a second run on the same day gets its start time added to the file name (`notebook_test_report_2026-10-22_143005.json`), or name it yourself with `--report-name`.

To rerun one notebook after a change, use the same work folder:

```bash
notebook-test run --path notebook-test-work/repos/notebooks/folder/example.ipynb --workdir notebook-test-work --report-name rerun-example-1
```

Environments built in a work folder are remembered there (`envs/kernels.json`), so the rerun uses the same environment as the full run. If the environment file changed since, the environment is updated first.

Protected files (`protect_files` in the profile) are restored after every notebook, because some notebooks save placeholder credentials over them; the report lists those notebooks.

```bash
notebook-test dashboard reports/*.json --out dashboard.html
notebook-test catalog reports/notebook_test_report_2026-10-19.json --out catalog_exclude.json
notebook-test relabel old_report.json --profile wekeo --out new_report.json
```

`catalog` lists notebooks whose errors carry a `blocking` label. `relabel` labels the errors of an existing report again with another profile (follow-on labels are kept). Reports from before `schema_version` 1 need `--profile` for `dashboard` and `catalog`.

## Profiles

Bundled profiles live in `src/notebook_test/profiles/`; the format is described by `profile.schema.json` there, and `generic.yml` documents every key.

| Section | What it sets |
|---|---|
| `labels` | Label id → display `text` and whether it is `blocking` (the notebook belongs on the exclude list). `notebook_error`, `follow_on`, `kernel_died` and `timeout` should exist. |
| `classify`, `classify_order` | Rules that give a cell error its label, by stage. First matching rule wins; otherwise `default_label`. |
| `rerun_on` | Labels that trigger one rerun of the notebook. |
| `protect_files` | Files restored after each notebook. |
| `exclude`, `report` | Texts of the exclude list, report and dashboard. |

A profile can `extends:` another (name, or path relative to the file). Mappings merge, other values replace, and a key ending in `+` appends to the parent's list: `classify: {platform+: [...]}`, `protect_files+: [~/.mine]`.

| Label (generic) | Meaning |
|---|---|
| notebook error | The notebook itself fails. The author has to fix it. |
| follow-on error | Caused by an earlier failed cell. Not listed in detail. |
| missing package | `ModuleNotFoundError`/`ImportError`: the environment lacks a package. |
| credentials | Login or permission failed, an auth URL (token, login) failed, or the notebook asked for input. |
| service unavailable | HTTP 5xx, timeouts, connection errors. The notebook is rerun once. |
| quota exceeded | The service refused because of a quota (`QuotaExceeded…` errors). |
| needs platform service | A service the platform provides is missing. Empty in `generic`; the `wekeo` profile fills it. |
| timeout / kernel died | A cell ran longer than `timeout`, or the kernel crashed (often memory). |

## Report format

`notebook_test_report_<date>.json`, described by `src/notebook_test/report.schema.json`. Readers should check `schema_version` (currently `1`); fields are only added within a version.

```text
schema_version  1
profile         name of the default profile
labels          {label id: {text, blocking}}      everything needed to read the labels
texts           {title, dashboard_title, error_types_note, protect_note, exclude_note}
started_at, finished_at, host
repos[]         repo, profile, url, commit, environments, results[]
  results[]     repo, notebook (path in the repo), status ("ok"/"error"), duration_s, kernel,
                executed_path / errored_path, error_message, reran, labels {id: count},
                changed_credentials[], cell_errors[]
    cell_errors[]  cell_number, code_cell_number, cell_preview, error_name, error_value,
                   traceback_last_lines, label
```

## WEkEO

Use the `wekeo` profile ([`src/notebook_test/profiles/wekeo.yml`](src/notebook_test/profiles/wekeo.yml)): it adds the "needs JupyterHub service" label (Dask Gateway, NVIDIA GPU, Ollama), the HDA token/identity endpoints as credential problems, and protects `~/.hdarc`, `~/.cdsapirc`, `~/.ecmwfdatastoresrc` and `~/.eumdac/credentials`. The exclude list is only valid for runs on the WEkEO JupyterHub.

## Origin and licence

MIT, see `LICENSE`. The runner builds on the DEx team's `AutoChecksV3.py` and `env_create.txt` (wekeo-dex, MIT). Dependencies: nbformat, nbclient, jupyter_client (BSD-3-Clause), PyYAML and jsonschema (MIT).
