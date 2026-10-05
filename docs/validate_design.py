"""Document-only validation. Does not exercise or provision the bot."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EXPECTED = {f"R{i:02}" for i in range(1, 23)}


def heading_ids(text: str) -> set[str]:
    result = set()
    for line in text.splitlines():
        if re.match(r"^#{1,6} ", line):
            label = re.sub(r"^#{1,6} ", "", line).strip().lower()
            label = re.sub(r"[^\w\- ]", "", label).replace(" ", "-")
            result.add(label)
    return result


def main() -> None:
    issues: list[str] = []
    files = [ROOT / "README.md"] + [
        DOCS / name
        for name in ["REQUIREMENTS.md", "HLD.md", "LLD.md", "ACCEPTANCE.md", "DESIGN_CHECKS.md"]
    ]
    required_rows = re.findall(r"^\| (R\d{2}) \|", (DOCS / "REQUIREMENTS.md").read_text(encoding="utf-8"), re.M)
    covered_rows = re.findall(r"^\| (R\d{2}) [—-]", (DOCS / "ACCEPTANCE.md").read_text(encoding="utf-8"), re.M)
    for name, rows in [("requirements", required_rows), ("acceptance", covered_rows)]:
        if set(rows) != EXPECTED or len(rows) != len(EXPECTED):
            issues.append(f"{name}: missing or duplicate requirement IDs")
    link_count = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        if sum(line.startswith("```") for line in text.splitlines()) % 2:
            issues.append(f"{path.name}: unbalanced code fences")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / file_part).resolve() if file_part else path
            link_count += 1
            if not dest.is_file():
                # The report is created from the first successful validation.
                if dest != (DOCS / "DESIGN_CHECKS.md").resolve():
                    issues.append(f"{path.name}: missing link {target}")
            elif anchor and anchor not in heading_ids(dest.read_text(encoding="utf-8")):
                issues.append(f"{path.name}: missing heading {target}")
    # Exact fictional walkthrough arithmetic. Integer values are paise.
    balances = {"pool": 1_000_000, "Travel": 0, "Food": 0}
    balances["pool"] -= 200_000
    balances["Travel"] += 200_000
    balances["Travel"] -= 40_000
    assert balances == {"pool": 800_000, "Travel": 160_000, "Food": 0}
    assert sum(balances.values()) == 960_000
    balances["Travel"] -= 180_000  # allowed expense overdraft
    assert balances["Travel"] == -20_000 and sum(balances.values()) == 780_000
    balances["Travel"] += 50_000  # pool -> negative bucket
    balances["pool"] -= 50_000
    assert sum(balances.values()) == 780_000
    # Income correction from 10,000 to 12,000 adds net 2,000 even if most
    # original income was allocated; a 2,000 downward correction needs funds.
    correction_net = 1_200_000 - 1_000_000
    assert correction_net == 200_000
    report = {
        "kind": "document_checks_not_application_tests",
        "documents_checked": len(files),
        "requirements_count": len(required_rows),
        "acceptance_requirements_count": len(covered_rows),
        "local_links_checked": link_count,
        "fictional_integer_money_examples": "passed",
        "issues": issues,
        "status": "passed" if not issues else "failed",
    }
    print(json.dumps(report, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
