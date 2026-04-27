import argparse
import shutil
from pathlib import Path

from ..core.io import ROOT


def remove_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean generated V2 files for a fresh topic run.")
    parser.add_argument("--include-papers", action="store_true", help="Also remove downloaded PDFs under v2/papers.")
    args = parser.parse_args()

    targets = [ROOT / "temp", ROOT / "final"]
    if args.include_papers:
        targets.append(ROOT / "papers")

    for target in targets:
        remove_path(target)
        print(f"Cleaned {target}")


if __name__ == "__main__":
    main()
