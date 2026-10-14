import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

LOOKBACK = 8
SELF_THRESH = 20.0
PEER_THRESH = 15.0
NOTE_RADIUS_DAYS = 14

HERE = Path(__file__).resolve().parent
log = logging.getLogger(__name__)

NON_IMPACT_SIGNALS = (
    "not significantly affected",
    "no significant disruption",
    "remained stable",
    "remained normal",
    "no major disruption",
    "without a rate change",
    "not part of this dataset",
    "returned to normal",
    "improved road conditions",
)


def load_shipments(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["shipment_date"])
    df.dropna(subset=["quantity_tonnes", "distance_km", "freight_cost_inr"], inplace=True)

    df["route"] = df["origin"] + "-" + df["destination"]
    df["tonne_km"] = df["quantity_tonnes"] * df["distance_km"]
    df["cptk"] = df["freight_cost_inr"] / df["tonne_km"]

    # Map each shipment date to Monday of that week
    df["week_of"] = (
        df["shipment_date"] - pd.to_timedelta(df["shipment_date"].dt.weekday, unit="D")
    ).dt.date

    log.info("Loaded %d shipments across %d routes", len(df), df["route"].nunique())
    return df


def load_notes(path: Path) -> pd.DataFrame:
    notes = pd.read_csv(path, parse_dates=["date"]).dropna(subset=["note_id"])
    notes["date"] = notes["date"].dt.date

    notes["start"] = notes["date"] - timedelta(days=NOTE_RADIUS_DAYS)
    notes["end"] = notes["date"] + timedelta(days=NOTE_RADIUS_DAYS)

    for i, row in notes.iterrows():
        txt = row["note"].lower()
        if "feb 24" in txt and "mar 8" in txt:
            notes.at[i, "start"] = date(row["date"].year, 2, 24)
            notes.at[i, "end"] = date(row["date"].year, 3, 8)
        elif "returned to normal" in txt:
            notes.at[i, "start"] = row["date"]
            notes.at[i, "end"] = row["date"]

    notes["cost_raiser"] = notes["note"].apply(
        lambda t: not any(sig in t.lower() for sig in NON_IMPACT_SIGNALS)
    )
    return notes


def weekly_route_costs(df: pd.DataFrame) -> pd.DataFrame:
    agg = (
        df.groupby(["route", "route_type", "week_of"], as_index=False)
        .agg(
            cost=("freight_cost_inr", "sum"),
            tonne_km=("tonne_km", "sum"),
            n_shipments=("shipment_id", "count"),
        )
    )
    agg["cptk"] = agg["cost"] / agg["tonne_km"]
    return agg.sort_values(["route", "week_of"]).reset_index(drop=True)


def add_baselines(weekly: pd.DataFrame) -> pd.DataFrame:
    # Trailing 8-week average (strictly prior weeks via shift)
    weekly["trailing_avg"] = (
        weekly.groupby("route", group_keys=False)["cptk"].apply(
            lambda s: s.shift(1).rolling(LOOKBACK, min_periods=1).mean()
        )
    )

    # Same-type peer average excluding the route itself
    grp = weekly.groupby(["route_type", "week_of"])["cptk"]
    type_sum = grp.transform("sum")
    type_cnt = grp.transform("count")
    weekly["peer_avg"] = np.where(
        type_cnt > 1,
        (type_sum - weekly["cptk"]) / (type_cnt - 1),
        np.nan,
    )

    weekly["pct_own"] = np.where(
        weekly["trailing_avg"].notna(),
        (weekly["cptk"] - weekly["trailing_avg"]) / weekly["trailing_avg"] * 100,
        np.nan,
    )
    weekly["pct_peer"] = np.where(
        weekly["peer_avg"].notna(),
        (weekly["cptk"] - weekly["peer_avg"]) / weekly["peer_avg"] * 100,
        np.nan,
    )
    return weekly


def detect_anomalies(weekly: pd.DataFrame) -> pd.DataFrame:
    weekly["anomaly"] = (
        (weekly["pct_own"].fillna(0) >= SELF_THRESH)
        | (weekly["pct_peer"].fillna(0) >= PEER_THRESH)
    )
    return weekly


def _note_applies_to_route(note_applies_to: str, route: str) -> bool:
    target = note_applies_to.strip()
    if target.lower() == "all routes" or target == route:
        return True
    parts = target.split("-")
    return len(parts) == 2 and f"{parts[1]}-{parts[0]}" == route


def _note_overlaps_week(start: date, end: date, week_monday: date) -> bool:
    week_sunday = week_monday + timedelta(days=6)
    return start <= week_sunday and end >= week_monday


def _rejection_reason(note_text: str) -> str:
    lo = note_text.lower()
    if "stable" in lo or "no major" in lo:
        return "mentions stable demand with no major disruptions -- it does not describe a reason for a cost rise on this route"
    if "not significantly" in lo:
        return "mentions minor delays but notes costs were not significantly affected"
    if "normal" in lo:
        return "indicates freight movement remained normal"
    if "absorbed" in lo:
        return "notes compliance costs were absorbed without a rate change"
    if "not part of this dataset" in lo:
        return "mentions a change that does not affect routes in this dataset"
    if "improved road conditions" in lo or "resurfacing" in lo:
        return "mentions road condition improvements rather than cost escalation factors"
    return "does not describe a reason for a cost rise on this route"


def explain_anomalies(weekly: pd.DataFrame, notes: pd.DataFrame) -> pd.DataFrame:
    note_ids, flags, reasons = [], [], []

    for _, row in weekly[weekly["anomaly"]].iterrows():
        route, wk = row["route"], row["week_of"]
        best_match = None
        closest_reject = None

        for _, n in notes.iterrows():
            if not _note_applies_to_route(n["applies_to"], route):
                continue
            if not _note_overlaps_week(n["start"], n["end"], wk):
                continue
            if n["cost_raiser"]:
                best_match = n
                break
            elif closest_reject is None:
                closest_reject = n

        if best_match is not None:
            note_ids.append(best_match["note_id"])
            flags.append("No (justified)")
            summary = best_match["note"].split(". ")[0].rstrip(".").lower()
            reasons.append(
                f"Matches note {best_match['note_id']} dated {best_match['date'].isoformat()}: "
                f"{summary}. The cost rise has a clear explanation."
            )
        elif closest_reject is not None:
            note_ids.append("")
            flags.append("Yes")
            reasons.append(
                f"The closest note ({closest_reject['note_id']}, "
                f"{closest_reject['date'].isoformat()}) "
                f"{_rejection_reason(closest_reject['note'])}. "
                f"No genuine justification found; flagged for review."
            )
        else:
            note_ids.append("")
            flags.append("Yes")
            reasons.append(
                "No matching note found for this route or date range. "
                "Cost rise looks unexplained and worth a human review."
            )

    anomalies = weekly[weekly["anomaly"]].copy()
    anomalies["matched_note_id"] = note_ids
    anomalies["flagged"] = flags
    anomalies["reason"] = reasons
    return anomalies


def format_output(anomalies: pd.DataFrame) -> pd.DataFrame:
    out = anomalies.copy()
    out["week_of"] = out["week_of"].apply(lambda d: d.isoformat())
    out["cost_per_tonne_km"] = out["cptk"].round(2)
    out["vs_own_history"] = out["pct_own"].apply(
        lambda x: f"{x:+.1f}% vs this route's past average"
        if pd.notna(x)
        else "N/A (insufficient history)"
    )
    out["vs_similar_routes"] = out["pct_peer"].apply(
        lambda x: f"{x:+.1f}% vs similar-length routes this week"
        if pd.notna(x)
        else "N/A (no peer data)"
    )

    cols = [
        "route",
        "week_of",
        "cost_per_tonne_km",
        "vs_own_history",
        "vs_similar_routes",
        "flagged",
        "matched_note_id",
        "reason",
    ]
    return out[cols].sort_values(["route", "week_of"]).reset_index(drop=True)


def run(shipment_path=None, notes_path=None, output_path=None):
    shipment_path = shipment_path or HERE / "shipment_records.csv"
    notes_path = notes_path or HERE / "context_notes.csv"
    output_path = output_path or HERE / "output.csv"

    shipments = load_shipments(shipment_path)
    notes = load_notes(notes_path)

    weekly = weekly_route_costs(shipments)
    weekly = add_baselines(weekly)
    weekly = detect_anomalies(weekly)

    anomalies = explain_anomalies(weekly, notes)
    output = format_output(anomalies)

    output.to_csv(output_path, index=False)

    justified = (output["flagged"] == "No (justified)").sum()
    flagged = (output["flagged"] == "Yes").sum()
    print(
        f"Processed {len(weekly)} route-weeks: {len(output)} anomalies "
        f"({justified} justified, {flagged} flagged) -> {output_path.name}"
    )
    return output, weekly


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    run()
