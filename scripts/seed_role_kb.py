"""Chunk, embed and index role KB seed files into kb_role and update kb_manifest.json.

Usage: python -m scripts.seed_role_kb [data_dir]
"""
import sys
from pathlib import Path

from app.rag.seeding import SeedError, seed_role_file, validate_dir


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    failures = validate_dir(root)
    if failures:
        print("validation failed:", failures)
        return 1
    files = sorted((root / "role_kb").glob("*.json"))
    if not files:
        print(f"no role seed files in {root / 'role_kb'}")
        return 1
    for path in files:
        try:
            print(path.stem, seed_role_file(path))
        except SeedError as e:
            print("ERROR", e)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
