import hashlib
import re
import sys
from pathlib import Path

import pandas as pd

import freight_tiger

HERE = Path(__file__).resolve().parent

SAMPLE_ROWS = [
    {
        "route": "Delhi-Jaipur",
        "week": "2024-11-11",
        "cptk": 4.17,
        "own": 35.5,
        "peer": 21.0,
        "flag": "Yes",
        "note": "",
    },
    {
        "route": "Ahmedabad-Mumbai",
        "week": "2025-01-20",
        "cptk": 3.29,
        "own": 29.5,
        "peer": 22.5,
        "flag": "No (justified)",
        "note": "N002",
    },
    {
        "route": "Mumbai-Pune",
        "week": "2025-09-15",
        "cptk": 3.98,
        "own": 9.2,
        "peer": 23.6,
        "flag": "Yes",
        "note": "",
    },
]

EXPECTED_COLUMNS = [
    "route",
    "week_of",
    "cost_per_tonne_km",
    "vs_own_history",
    "vs_similar_routes",
    "flagged",
    "matched_note_id",
    "reason",
]


def extract_pct(text: str) -> float | None:
    m = re.search(r"([+-]?\d+\.?\d*)%", str(text))
    return float(m.group(1)) if m else None


def test_schema(df: pd.DataFrame) -> bool:
    if list(df.columns) != EXPECTED_COLUMNS:
        print(f"  FAIL schema: columns {list(df.columns)} != {EXPECTED_COLUMNS}")
        return False
    print("  PASS schema: columns and ordering match sample_output_format exactly")
    return True


def test_sample_rows(df: pd.DataFrame) -> bool:
    all_ok = True
    for c in SAMPLE_ROWS:
        label = f"{c['route']} [{c['week']}]"
        row = df[(df["route"] == c["route"]) & (df["week_of"] == c["week"])]
        if row.empty:
            print(f"  FAIL {label}: row missing from output")
            all_ok = False
            continue

        r = row.iloc[0]
        errs = []
        if abs(r["cost_per_tonne_km"] - c["cptk"]) > 0.005:
            errs.append(f"cptk {r['cost_per_tonne_km']} != {c['cptk']}")

        got_own = extract_pct(r["vs_own_history"])
        if got_own is not None and abs(got_own - c["own"]) > 0.05:
            errs.append(f"own {got_own}% != {c['own']}%")

        got_peer = extract_pct(r["vs_similar_routes"])
        if got_peer is not None and abs(got_peer - c["peer"]) > 0.05:
            errs.append(f"peer {got_peer}% != {c['peer']}%")

        if r["flagged"] != c["flag"]:
            errs.append(f"flag '{r['flagged']}' != '{c['flag']}'")

        note = str(r["matched_note_id"]).strip()
        if note in ("nan", "None"):
            note = ""
        if note != c["note"]:
            errs.append(f"note '{note}' != '{c['note']}'")

        if errs:
            print(f"  FAIL {label}: {'; '.join(errs)}")
            all_ok = False
        else:
            print(f"  PASS {label}: cptk={c['cptk']}, own=+{c['own']}%, peer=+{c['peer']}%, flag='{c['flag']}'")

    return all_ok


def test_guardrails(df: pd.DataFrame, notes_path: Path) -> bool:
    notes = pd.read_csv(notes_path)
    all_ok = True

    # 1. Flagged rows must never cite a note
    flagged_with_note = df[(df["flagged"] == "Yes") & df["matched_note_id"].fillna("").astype(str).str.strip().ne("")]
    if not flagged_with_note.empty:
        print(f"  FAIL guardrails: {len(flagged_with_note)} flagged rows cite a note (must be blank)")
        all_ok = False
    else:
        print("  PASS guardrails: all unexplained flagged rows leave matched_note_id blank")

    # 2. Justified rows must cite an existing, valid note
    justified = df[df["flagged"] == "No (justified)"]
    valid_note_ids = set(notes["note_id"])
    for _, r in justified.iterrows():
        nid = str(r["matched_note_id"]).strip()
        if nid not in valid_note_ids:
            print(f"  FAIL guardrails: row {r['route']} {r['week_of']} cites unknown note '{nid}'")
            all_ok = False
        if nid not in r["reason"]:
            print(f"  FAIL guardrails: reason prose does not reference cited note {nid}")
            all_ok = False

    print(f"  PASS guardrails: all {len(justified)} justified rows cite valid context notes in table and reason")

    # 3. Disruption non-impact notes must never be cited as justification
    non_impact_ids = {"N004", "N005", "N006", "N007", "N008", "N009", "N010"}
    bad_cites = set(justified["matched_note_id"]).intersection(non_impact_ids)
    if bad_cites:
        print(f"  FAIL guardrails: non-impact notes cited as justification: {bad_cites}")
        all_ok = False
    else:
        print(f"  PASS guardrails: zero non-impact notes cited (rejection filter active)")

    return all_ok


def test_reproducibility() -> bool:
    hashes = []
    for _ in range(3):
        out, _ = freight_tiger.run()
        h = hashlib.sha256(out.to_csv(index=False).encode("utf-8")).hexdigest()
        hashes.append(h)

    if len(set(hashes)) == 1:
        print(f"  PASS reproducibility: 3 identical runs (SHA-256: {hashes[0][:16]}...)")
        return True

    print(f"  FAIL reproducibility: runs produced divergent outputs: {hashes}")
    return False


def main():
    print("=" * 60)
    print("FREIGHT TIGER PIPELINE TEST SUITE")
    print("=" * 60)

    output_path = HERE / "output.csv"
    if not output_path.exists():
        freight_tiger.run()

    df = pd.read_csv(output_path)

    results = [
        ("Schema & Contract", test_schema(df)),
        ("Sample Rows Numerical Accuracy", test_sample_rows(df)),
        ("Hallucination & Guardrails", test_guardrails(df, HERE / "context_notes.csv")),
        ("Reproducibility (3 Runs)", test_reproducibility()),
    ]

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        print(f"  {name:<35} : {status}")
        if not passed:
            all_passed = False

    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
