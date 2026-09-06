"""Generates the derived permission-catalog artifacts from the single
canonical source, app/core/permissions_catalog.json.

Usage:
    python scripts/gen_permissions.py            # write the derived files
    python scripts/gen_permissions.py --check    # exit 1 with a diff if any
                                                  # derived file is stale

Derived files:
- back/policies/data/permissions.json  — same role->permissions shape, for
  OPA to load as `data` once the rego is wired to consume it (not yet done;
  see module roadmap Wave A2 for why that's a separate, larger change).
- front/src/permissions.generated.js   — same shape as a plain JS object,
  consumed by front/src/store.js's COMPOSITE_ROLE_PERMISSIONS import.

The docs table (back/docs/reference/PERMISSIONS_CATALOG_REFERENCE.md) is
hand-curated editorial content (personas, highlight selections) and is
deliberately NOT machine-generated -- see the canonical-source note this
script's sibling task adds atop that table instead.
"""

import argparse
import json
import sys
from pathlib import Path

BACK_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACK_DIR.parent

CANONICAL = BACK_DIR / "app" / "core" / "permissions_catalog.json"
OPA_DATA = BACK_DIR / "policies" / "data" / "permissions.json"
FRONTEND_JS = REPO_ROOT / "front" / "src" / "permissions.generated.js"

GENERATED_HEADER = "// GENERATED — DO NOT EDIT. Source: back/app/core/permissions_catalog.json\n"
GENERATED_FOOTER = "// END GENERATED\n"


def _load_canonical() -> dict[str, list[str]]:
    return json.loads(CANONICAL.read_text(encoding="utf-8"))


def _render_opa_data(catalog: dict[str, list[str]]) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def _render_frontend_js(catalog: dict[str, list[str]]) -> str:
    lines = [GENERATED_HEADER, "export const COMPOSITE_ROLE_PERMISSIONS = {\n"]
    for role in sorted(catalog):
        perms = ", ".join(json.dumps(p) for p in sorted(catalog[role]))
        lines.append(f"  {role}: [{perms}],\n")
    lines.append("};\n")
    lines.append(GENERATED_FOOTER)
    return "".join(lines)


def _targets() -> dict[Path, str]:
    catalog = _load_canonical()
    return {
        OPA_DATA: _render_opa_data(catalog),
        FRONTEND_JS: _render_frontend_js(catalog),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    targets = _targets()

    if not args.check:
        for path, content in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        return

    stale = []
    for path, content in targets.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != content:
            stale.append((path, existing, content))

    if stale:
        for path, existing, content in stale:
            print(f"STALE: {path.relative_to(REPO_ROOT)}")
            if existing is None:
                print("  (file does not exist)")
            else:
                import difflib

                diff = difflib.unified_diff(
                    (existing or "").splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile="checked-in",
                    tofile="generated",
                )
                sys.stdout.writelines(diff)
        sys.exit(1)

    print("all generated files up to date")


if __name__ == "__main__":
    main()
