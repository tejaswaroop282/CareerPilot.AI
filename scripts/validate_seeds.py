"""Check seed JSON: schema, duplicates, required metadata. Exit code 1 on any problem.

Usage: python -m scripts.validate_seeds [data_dir]
"""
import sys
from pathlib import Path

from app.rag.seeding import validate_dir


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    files = list((root / "role_kb").glob("*.json")) + list((root / "company_kb").glob("*.json"))
    failures = validate_dir(root)
    for name, errors in failures.items():
        print(f"FAIL {name}")
        for e in errors:
            print(f"   - {e}")
    print(f"{len(files) - len(failures)}/{len(files)} seed files valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
