#!/usr/bin/env python3
"""Departure diagnostics and hypothetical lapse-rate sensitivity for paired
urban/reference observations.

The lapse-rate adjustment dT_gamma = dT - gamma*(z_ref - z_site)/1000 is a
labelled HYPOTHETICAL scenario bracket, NOT a measured atmospheric correction.

Usage:
  python3 lapse_sensitivity.py --paired PAIRED.csv --sites SITES.csv \
      --ref-elevation 262.0 --outdir OUTDIR [--lapse-rates 5.0 6.5 9.8]

paired CSV columns: site_id, band, month, hour, delta_T_K [, delta_e_Pa]
sites CSV columns:  site_id, elevation_m
"""
import argparse, json, sys, os
from pathlib import Path
import numpy as np
import pandas as pd


def compute_lapse_sensitivity(paired, sites, ref_elevation, lapse_rates):
    """Return a DataFrame of hypothetical lapse-rate adjusted departures."""
    merged = paired.merge(sites, on="site_id", how="left", validate="many_to_one")
    if merged["elevation_m"].isna().any():
        missing = merged.loc[merged["elevation_m"].isna(), "site_id"].unique()
        raise ValueError(f"sites CSV missing elevation for: {list(missing)}")
    merged["delta_z_km"] = (ref_elevation - merged["elevation_m"]) / 1000.0
    rows = []
    for gamma in [0.0] + list(lapse_rates):
        col = f"delta_T_gamma_{gamma}"
        merged[col] = merged["delta_T_K"] - gamma * merged["delta_z_km"]
        for band in sorted(merged["band"].unique()):
            sub = merged[merged["band"] == band]
            per_site = sub.groupby("site_id")[col].mean()
            rows.append({"gamma_K_per_km": gamma, "band": band,
                         "equal_site_mean_K": round(per_site.mean(), 4),
                         "n_sites": len(per_site),
                         "n_rows": len(sub)})
        all_per_site = merged.groupby("site_id")[col].mean()
        rows.append({"gamma_K_per_km": gamma, "band": "ALL",
                     "equal_site_mean_K": round(all_per_site.mean(), 4),
                     "n_sites": len(all_per_site), "n_rows": len(merged)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--paired", required=True, help="paired CSV path")
    ap.add_argument("--sites", required=True, help="sites CSV path (site_id, elevation_m)")
    ap.add_argument("--ref-elevation", type=float, required=True, help="reference elevation (m)")
    ap.add_argument("--outdir", required=True, help="output directory")
    ap.add_argument("--lapse-rates", nargs="+", type=float, default=[5.0, 6.5, 9.8],
                    help="lapse rates K/km (default: 5.0 6.5 9.8)")
    args = ap.parse_args()

    paired = pd.read_csv(args.paired)
    sites = pd.read_csv(args.sites)
    for col in ("site_id", "band", "month", "hour", "delta_T_K"):
        if col not in paired.columns:
            raise ValueError(f"paired CSV missing column: {col}")
    if "site_id" not in sites.columns or "elevation_m" not in sites.columns:
        raise ValueError("sites CSV must have site_id and elevation_m")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    result = compute_lapse_sensitivity(paired, sites, args.ref_elevation, args.lapse_rates)
    result.to_csv(outdir / "lapse_sensitivity.csv", index=False)
    summary = {
        "ref_elevation_m": args.ref_elevation,
        "lapse_rates_K_per_km": args.lapse_rates,
        "n_paired_rows": len(paired),
        "n_sites": sites["site_id"].nunique(),
        "note": "HYPOTHETICAL scenario brackets, NOT a measured correction",
        "gamma_0_mean_K": round(result[result.gamma_K_per_km == 0.0]["equal_site_mean_K"].iloc[0], 4),
    }
    for g in args.lapse_rates:
        row = result[(result.gamma_K_per_km == g) & (result.band == "ALL")]
        if len(row):
            summary[f"gamma_{g}_mean_K"] = round(row["equal_site_mean_K"].iloc[0], 4)
    with open(outdir / "lapse_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
