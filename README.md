# wekeo-notebook-qa

Checks and execution tests for the WEkEO Jupyter notebooks.

- **Checks on every change (`wnqa check` and a reusable GitHub workflow).** They test the notebook metadata against the EUMETSAT Jupyter notebook metadata schema 2.0.0, the WEkEO JupyterHub launch badge, unattended credentials and secrets in stored outputs. The workflow also runs the ECMWF `notebook-qa` checks (ruff, pynblint, links, accessibility, figure attribution, LICENSE, CHANGELOG).
- **Execution test every quarter (`wnqa run`).** It runs every cell of every notebook, keeps going after errors and labels each error, then writes a JSON and Markdown report. `wnqa dashboard` turns reports into a web page, and `wnqa catalog` lists notebooks to keep out of the WEkEO Jupyter Catalog.

The tools do not change notebooks and do not open issues.

## Install

```bash
pip install git+https://github.com/annaerdmann/wekeo-notebook-qa
```

Python 3.10 or newer. Building environments (`environments: build`) also needs micromamba, mamba or conda.

## Checks (`wnqa check`)

```bash
wnqa check                      # all notebooks below the current folder
wnqa check path/to/nb.ipynb     # selected notebooks or folders
wnqa check --json findings.json --markdown findings.md
```

Exit code 1 if there is an ERROR.

| Check | Level | What it tests |
|---|---|---|
| `metadata` | ERROR | Fields mandatory for the WEkEO Jupyter Catalog: `author`, `deployment`, `description` (≤180 chars), `image`, `originator`, `title` (≤70 chars), `tags:data_provider`, `tags:theme` (Atmosphere, Climate, Tools, Marine, Land, Machine Learning). Deployment links must be filled and not `TOBEFILLED`. Other metadata fields are accepted. |
| `metadata` | WARN | `metadata_schema_version`, `license`, `version_date` (YYYY-MM-DD), a deployment link to WEkEO. |
| `launch_badge` | ERROR | A Markdown cell links to the WEkEO JupyterHub, and the link is not a placeholder. |
| `credentials` | ERROR | No placeholders such as `<your_user_name>` in code, and no `input()`/`getpass()` unless the cell first tries `~/.hdarc`. |
| `secrets` | ERROR | No tokens, keys or passwords in stored outputs. |
| `environment` | WARN | An `environment.yml` between the notebook and the repository root. |

### Configuration

Put `.github/notebook-qa.yml` in the repository. It uses the same keys as the ECMWF workflow, so one file configures both:

```yaml
disabled_checks: [environment]
skip_notebooks: ["drafts/**"]
notebooks:
  "path/to/nb.ipynb":
    skip: [launch_badge]
```

For WEkEO repositories, also disable the ECMWF `execute` and `data_source` checks. Execution runs on the JupyterHub instead (`wnqa run`), and the ECMWF data source check only knows CDS/ADS:

```yaml
disabled_checks: [execute, data_source]
```

### GitHub workflow

```yaml
# .github/workflows/notebook-qa.yml in a WEkEO repository
name: Notebook QA
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pull-requests: write
jobs:
  qa:
    uses: annaerdmann/wekeo-notebook-qa/.github/workflows/notebook-qa.yml@main
    secrets: inherit
```

While this repository is private, other repositories can only use the workflow if they belong to the same owner and *Settings > Actions > General > Access* allows it. Move it to the WEkEO organisation (and update `wnqa_repo`) before using it in WEkEO repositories.

On pull requests only changed notebooks are checked, plus every mirrored repository (submodule) whose pointer changed. The ECMWF checks do not check out submodules, so for mirrored notebooks only the WEkEO checks run.

## Execution test (`wnqa run`)

```bash
wnqa run config.yml             # repos from a config file
wnqa run --path some/folder     # local notebooks, default kernel
```

Example `config.yml`:

```yaml
workdir: ~/wnqa-work      # clones, environments, executed notebooks and reports
timeout: 1200             # seconds per cell
kernel: auto              # notebook's own kernel if installed, else python3 (ir for R)
rerun_wait: 60            # wait before rerunning a notebook after a service error
repos:
  - url: https://github.com/wekeo/wekeo4data
  - url: https://github.com/wekeo/wekeo4land
    environments: build   # build a conda env per environment.yml and use it
    skip: ["**/old/**"]
```

Mirrored repositories (submodules) are cloned too. Reports go to `<workdir>/reports/`. Executed notebooks go to `<workdir>/outputs/`; they can contain outputs from your account, so do not publish them. Remove the work folder to clean up everything, environments included.

Each error gets a label:

| Label | Meaning |
|---|---|
| notebook error | The notebook itself fails. The author has to fix it. |
| follow-on error | Caused by an earlier failed cell (e.g. `NameError` for a variable that cell should have set). Not listed in detail. |
| missing package | `ModuleNotFoundError`/`ImportError`: the environment lacks a package. |
| credentials | Login or permission failed, or the notebook asked for input. |
| service unavailable | HTTP 5xx, timeouts, connection errors. The notebook is rerun once. |
| needs JupyterHub service | Dask Gateway, a GPU or the Ollama server is missing. Expected on a VM or laptop; on the JupyterHub it is a real failure. |
| timeout / kernel died | A cell ran longer than `timeout`, or the kernel crashed (often memory). |

### Dashboard and catalog list

```bash
wnqa dashboard reports/*.json --out dashboard.html
wnqa catalog reports/notebook_test_report_2026-10-19.json --out catalog_exclude.json
```

The catalog list holds notebooks with notebook errors, missing packages, timeouts or crashes. Service outages, credential problems and missing JupyterHub services do not count. It is only meaningful for runs on the WEkEO JupyterHub.

## Origin and licence

MIT, see `LICENSE`. The runner builds on the DEx team's `AutoChecksV3.py` and `env_create.txt` (wekeo-dex, MIT). The configuration format follows ECMWF's `ecmwf-training/reusable-workflows`. Dependencies: nbformat, nbclient, jupyter_client (BSD-3-Clause) and PyYAML (MIT).
