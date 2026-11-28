"""Collect the licences of every Go module linked into the EEBUS service (eebus/THIRD_PARTY_LICENSES.txt).

Run after changing eebus/go.mod: python3 tools/eebus_licences.py (needs the Go toolchain on PATH).
The App image ships the file: a compiled program carries the code of all its modules.
"""

import json
from pathlib import Path
import subprocess

EEBUS = Path(__file__).resolve().parents[1] / "eebus"
NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE")


def main() -> None:
    goroot = subprocess.run(["go", "env", "GOROOT"], capture_output=True, text=True, check=True).stdout.strip()
    listing = subprocess.run(
        ["go", "list", "-deps", "-json", "."], cwd=EEBUS, capture_output=True, text=True, check=True
    ).stdout
    modules = {}
    for chunk in listing.replace("}\n{", "}\x00{").split("\x00"):
        module = json.loads(chunk).get("Module")
        if module and not module.get("Main"):
            modules[module["Path"]] = (module["Version"], Path(module["Dir"]))
    parts = [
        "Third-party licences of the Sunflow EEBUS service\n",
        "The program is built from these Go modules and the Go standard library.\n",
        f"\n{'=' * 78}\nGo standard library (https://go.dev)\n{'=' * 78}\n\n{(Path(goroot) / 'LICENSE').read_text()}",
    ]
    for path, (version, directory) in sorted(modules.items()):
        texts = [(directory / name).read_text() for name in NAMES if (directory / name).is_file()]
        if not texts:
            raise SystemExit(f"No licence file in {path}")
        parts.append(f"\n{'=' * 78}\n{path} {version}\n{'=' * 78}\n\n" + "\n".join(texts))
    (EEBUS / "THIRD_PARTY_LICENSES.txt").write_text("".join(parts))
    print(f"{len(modules)} modules")


if __name__ == "__main__":
    main()
