#!/usr/bin/env python3
"""Restore bundled text resources omitted by a distribution file-type filter."""
import json
from pathlib import Path


RESOURCES = (
    "assets/styles/science.mplstyle",
    "assets/styles/no-latex.mplstyle",
    "assets/styles/ye1-clinical.mplstyle",
    "assets/styles/ye1-mono.mplstyle",
    "scripts/restyle_ggplot.R",
)


def main():
    root = Path(__file__).resolve().parent.parent
    missing = [name for name in RESOURCES
               if not (root / name).is_file()
               and not (root / (name + ".txt")).is_file()]
    if missing:
        raise SystemExit("Missing bundled resources and text copies: " + ", ".join(missing))
    restored = []
    for name in RESOURCES:
        target = root / name
        if target.exists():
            continue
        data = (root / (name + ".txt")).read_bytes()
        try:
            with target.open("xb") as output:
                output.write(data)
        except FileExistsError:
            continue
        restored.append(name)
    print(json.dumps({"restored": restored}, ensure_ascii=False))


if __name__ == "__main__":
    main()
