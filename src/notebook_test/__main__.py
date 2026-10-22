"""Command line entry point: `notebook-test run ...`."""

import sys

USAGE = """usage: notebook-test <command> [options]

commands:
  run        execute notebooks and write a test report
  dashboard  build a dashboard page from run reports
  catalog    list notebooks to keep out of the catalog
  relabel    label the errors of an existing report again with another profile
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "run":
        from .run import main as run
    elif command == "dashboard":
        from .publish import main_dashboard as run
    elif command == "catalog":
        from .publish import main_catalog as run
    elif command == "relabel":
        from .publish import main_relabel as run
    else:
        print(USAGE)
        return 2
    return run(rest)


if __name__ == "__main__":
    sys.exit(main())
