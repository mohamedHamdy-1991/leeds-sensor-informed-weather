#!/usr/bin/env python3
"""Departure diagnostics and hypothetical lapse-rate sensitivity for paired
urban/reference observations (reference implementation from the Leeds study).

Expected inputs (CSV):
  paired : sensor_id, band, month, hour, delta_T_K  (and optionally delta_e_Pa)
  sites  : sensor_id, elevation_m
Run:  python3 lapse_sensitivity.py --paired PAIRED.csv --sites SITES.csv         --ref-elevation 262.0 --outdir OUTDIR

Outputs departure summaries (band, season, hour x season), per-device RH
diagnostics if a rh_gt100 column is present, and the HYPOTHETICAL lapse-rate
sensitivity dT_gamma = dT - gamma*(z_ref - z_site)/1000 for gamma in
{5, 6.5, 9.8} K/km plus the gamma=0 comparator. The lapse-rate adjustment is a
labelled hypothetical scenario, NOT a measured correction.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

COHORT = Path("/Volumes/Mo.Hamdy/sensor Informed/FIGURE_REDESIGN_2026-10-20/"
              "BAND_UPDATE_STAGE4_2026-10-21/derived/QUALIFIED_COHORT_V2_common_band")
META = Path("/Users/mohamedali/Library/CloudStorage/OneDrive-LeedsBeckettUniversity/"
            "Work/Ph.D/Publications/Sensor-Calibrated/Publication_Workflow_NEW/02_DATA/raw/"
            "sensor_processing/outputs/Leeds_Sensors_Metadata.csv")
BINGLEY_MIDAS = Path("/Users/mohamedali/Library/CloudStorage/OneDrive-LeedsBeckettUniversity/"
                     "Work/Ph.D/Publications/Sensor-Calibrated/data/EPW/"
                     "midas-open_uk-hourly-weather-obs_dv-202507_west-yorkshire_"
                     "00513_bingley-no-2_qcv-1_2024.csv")
OUT = Path("./outputs/wp2")
OUT.mkdir(parents=True, exist_ok=True)

Z_REF = 262.0  # Bingley station height, m; verified from MIDAS prolog height,G,262,m
BANDS = ["Centre", "Inner", "Outer", "Rural"]
BAND_RANK = {b: i for i, b in enumerate(BANDS)}
SEASON_OF_MONTH = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
                   6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
SEASONS = ["DJF", "MAM", "JJA", "SON"]

summary = {"inputs": {}, "notes": [], "spot_check": {}}


def _keys(df, keys):
    if len(keys) == 0:
        return df.assign(overall="overall"), ["overall"]
    return df, keys


def eq_dev(df, valcol, keys):
    """Equal-device weighting: mean within device per key group, then unweighted mean over devices."""
    df, keys = _keys(df, keys)
    d = df.groupby(keys + ["sensor_id"], observed=True)[valcol].mean().reset_index()
    g = d.groupby(keys, observed=True)[valcol].agg(eq_mean="mean", n_devices="count")
    return g


def row_wt(df, valcol, keys):
    df, keys = _keys(df, keys)
    g = df.groupby(keys, observed=True)[valcol].agg(row_mean="mean", n_rows="count")
    return g


def write_csv(df, name, units_col="units"):
    df.to_csv(OUT / name, index=True)
    print(f"[write] {name}  rows={len(df)}")


# ---------------------------------------------------------------- load inputs
print("loading paired_T_primary ...")
t = pd.read_csv(COHORT / "paired_T_primary.csv.gz", dtype={"sensor_id": str},
                usecols=["sensor_id", "serial", "band", "time_utc",
                         "t_sensor_degC", "t_ref_degC", "delta_T_K"],
                parse_dates=["time_utc"])
print("loading paired_e_primary ...")
e = pd.read_csv(COHORT / "paired_e_primary.csv.gz", dtype={"sensor_id": str},
                usecols=["sensor_id", "band", "time_utc", "t_ref_degC", "delta_e_Pa"],
                parse_dates=["time_utc"])
print("loading paired_e_derived ...")
ed = pd.read_csv(COHORT / "paired_e_derived.csv.gz", dtype={"sensor_id": str},
                 usecols=["sensor_id", "rh_gt100_sensor", "rh_gt100_ref"])
# flags may be written as True/False/true/false — normalise to bool explicitly
for c in ("rh_gt100_sensor", "rh_gt100_ref"):
    ed[c] = ed[c].astype(str).str.lower().eq("true")
print("loading metadata ...")
meta = pd.read_csv(META)
meta["sensor_id"] = meta["sensorid"].astype(str).str.strip()
elev = meta.set_index("sensor_id")["elevationm"].to_dict()
serial_meta = meta.set_index("sensor_id")["serial"].astype(str).to_dict()

n_T, n_e, n_ed = len(t), len(e), len(ed)
n_dev_T = t["sensor_id"].nunique()
n_dev_e = e["sensor_id"].nunique()
print(f"rows: T={n_T}, e_primary={n_e}, e_derived={n_ed}; devices T={n_dev_T} e={n_dev_e} "
      f"metadata={len(meta)}")

t["month"] = t["time_utc"].dt.month
t["hour"] = t["time_utc"].dt.hour
t["season"] = t["month"].map(SEASON_OF_MONTH)
t["band"] = pd.Categorical(t["band"], categories=BANDS, ordered=True)
e["month"] = e["time_utc"].dt.month
e["hour"] = e["time_utc"].dt.hour
e["season"] = e["month"].map(SEASON_OF_MONTH)
e["band"] = pd.Categorical(e["band"], categories=BANDS, ordered=True)

# sanity: delta consistency on a sample
samp = t.sample(5000, random_state=0)
max_dev = (samp["t_sensor_degC"] - samp["t_ref_degC"] - samp["delta_T_K"]).abs().max()
summary["spot_check"]["delta_T_identity_max_abs_dev_K_5000rows"] = float(max_dev)

summary["inputs"] = {
    "paired_T_primary.csv.gz": {"path": str(COHORT / "paired_T_primary.csv.gz"),
                                "data_rows": n_T, "units": "delta_T_K = K"},
    "paired_e_primary.csv.gz": {"path": str(COHORT / "paired_e_primary.csv.gz"),
                                "data_rows": n_e, "units": "delta_e_Pa = Pa"},
    "paired_e_derived.csv.gz": {"path": str(COHORT / "paired_e_derived.csv.gz"),
                                "data_rows": n_ed},
    "Leeds_Sensors_Metadata.csv": {"path": str(META), "rows": int(len(meta))},
    "Bingley MIDAS 00513 prolog": {"path": str(BINGLEY_MIDAS),
                                   "height_m": 262,
                                   "evidence": "prolog line 'height,G,262,m'; "
                                               "midas_station_id=00513; bingley-no-2"},
}

# ---------------------------------------------------------------- T1
print("T1 ...")
t1_rows = []
# overall
ov_eq = eq_dev(t, "delta_T_K", []).iloc[0]
ov_rw = row_wt(t, "delta_T_K", []).iloc[0]
t1_rows.append(("overall", "overall", float(ov_eq["eq_mean"]), int(ov_eq["n_devices"]),
                float(ov_rw["row_mean"]), int(ov_rw["n_rows"])))
ov_eq_e = eq_dev(e, "delta_e_Pa", []).iloc[0]
ov_rw_e = row_wt(e, "delta_e_Pa", []).iloc[0]
t1_rows_e = [("overall", "overall", float(ov_eq_e["eq_mean"]), int(ov_eq_e["n_devices"]),
              float(ov_rw_e["row_mean"]), int(ov_rw_e["n_rows"]))]

for b in BANDS:
    s_eq = eq_dev(t, "delta_T_K", ["band"]).loc[b]
    s_rw = row_wt(t, "delta_T_K", ["band"]).loc[b]
    t1_rows.append((b, "band", float(s_eq["eq_mean"]), int(s_eq["n_devices"]),
                    float(s_rw["row_mean"]), int(s_rw["n_rows"])))
t1 = pd.DataFrame(t1_rows, columns=["stratum", "level", "mean_dT_equal_device_K",
                                    "n_devices", "mean_dT_row_weighted_K", "n_rows"])
t1.to_csv(OUT / "T1_departure_band_overall_T.csv", index=False)
print(t1)

for b in BANDS:
    s_eq = eq_dev(e, "delta_e_Pa", ["band"]).loc[b]
    s_rw = row_wt(e, "delta_e_Pa", ["band"]).loc[b]
    t1_rows_e.append((b, "band", float(s_eq["eq_mean"]), int(s_eq["n_devices"]),
                      float(s_rw["row_mean"]), int(s_rw["n_rows"])))
t1e = pd.DataFrame(t1_rows_e, columns=["stratum", "level", "mean_de_equal_device_Pa",
                                       "n_devices", "mean_de_row_weighted_Pa", "n_rows"])
t1e.to_csv(OUT / "T1_departure_band_overall_e.csv", index=False)
print(t1e)

# season
t1s = pd.concat([eq_dev(t, "delta_T_K", ["season"]).add_prefix("dT_eq_"),
                 row_wt(t, "delta_T_K", ["season"]).add_prefix("dT_rw_")], axis=1)
t1s_e = pd.concat([eq_dev(e, "delta_e_Pa", ["season"]).add_prefix("de_eq_"),
                   row_wt(e, "delta_e_Pa", ["season"]).add_prefix("de_rw_")], axis=1)
t1s = t1s.reindex(SEASONS).join(t1s_e.reindex(SEASONS))
t1s.to_csv(OUT / "T1_departure_season.csv", index=True)
print(t1s)

# hour x season x band
hs = eq_dev(t, "delta_T_K", ["band", "season", "hour"]).reset_index()
hsrw = row_wt(t, "delta_T_K", ["band", "season", "hour"]).reset_index()
hs = hs.merge(hsrw, on=["band", "season", "hour"], how="outer")
hs.to_csv(OUT / "T1_hour_season_band_equal_device_T.csv", index=False)
write_csv(hs.set_index(["band", "season", "hour"]), "T1_hour_season_band_equal_device_T.csv")
# e version (hour x season overall for reference)
hse = eq_dev(e, "delta_e_Pa", ["band", "season", "hour"]).reset_index()
hse.to_csv(OUT / "T1_hour_season_band_equal_device_e.csv", index=False)

summary["T1"] = {
    "overall_delta_T_equal_device_K": float(ov_eq["eq_mean"]),
    "overall_delta_T_row_weighted_K": float(ov_rw["row_mean"]),
    "overall_delta_e_equal_device_Pa": float(ov_eq_e["eq_mean"]),
    "overall_delta_e_row_weighted_Pa": float(ov_rw_e["row_mean"]),
    "n_rows_T": n_T, "n_rows_e": n_e, "n_devices_T": n_dev_T, "n_devices_e": n_dev_e,
    "band_delta_T_equal_device_K": {b: float(eq_dev(t, "delta_T_K", ["band"]).loc[b, "eq_mean"])
                                    for b in BANDS},
    "band_delta_T_row_weighted_K": {b: float(row_wt(t, "delta_T_K", ["band"]).loc[b, "row_mean"])
                                    for b in BANDS},
    "manuscript_headlines": {"overall_dT_K": 1.73, "overall_de_Pa": 138,
                             "bands_K": {"Centre": 2.39, "Inner": 1.76, "Outer": 1.32,
                                         "Rural": 1.24}},
}

# ---------------------------------------------------------------- T2
print("T2 ...")
# per-band elevation stats from metadata
meta["band_rank"] = meta["sector"].map(BAND_RANK)
el = meta.groupby("sector")["elevationm"].agg(n_sites="count", mean_m="mean", sd_m="std",
                                              min_m="min", max_m="max")
el.loc["ALL"] = [meta["elevationm"].count(), meta["elevationm"].mean(),
                 meta["elevationm"].std(), meta["elevationm"].min(), meta["elevationm"].max()]
el["units"] = "metres above sea level (m)"
el.to_csv(OUT / "T2_band_elevation_stats.csv")
print(el)

# within-band spread + band-rank correlation
dev = t.groupby("sensor_id").agg(mean_dT_K=("delta_T_K", "mean"), band=("band", "first"),
                                 n=("delta_T_K", "count"))
dev["elevation_m"] = dev.index.map(elev)
dev["serial"] = dev.index.map(serial_meta)
dev = dev.dropna(subset=["elevation_m"])
dev["band_rank"] = dev["band"].astype(str).map(BAND_RANK)

r_elev_rank = float(np.corrcoef(dev["elevation_m"], dev["band_rank"])[0, 1])
r_dT_elev = float(np.corrcoef(dev["mean_dT_K"], dev["elevation_m"])[0, 1])
# point-biserial (Centre vs non-Centre) and Rural vs non-Rural variants
bin_c = (dev["band_rank"] == 0).astype(float)
r_pb_centre = float(np.corrcoef(dev["elevation_m"], bin_c)[0, 1])
bin_r = (dev["band_rank"] == 3).astype(float)
r_pb_rural = float(np.corrcoef(dev["elevation_m"], bin_r)[0, 1])
t2corr = pd.DataFrame([
    {"metric": "pearson_r_elevation_vs_band_rank_0to3", "value": r_elev_rank,
     "n_devices": len(dev), "units": "dimensionless"},
    {"metric": "pearson_r_per_device_mean_dT_vs_elevation", "value": r_dT_elev,
     "n_devices": len(dev), "units": "dimensionless"},
    {"metric": "point_biserial_r_elevation_vs_Centre_flag", "value": r_pb_centre,
     "n_devices": len(dev), "units": "dimensionless"},
    {"metric": "point_biserial_r_elevation_vs_Rural_flag", "value": r_pb_rural,
     "n_devices": len(dev), "units": "dimensionless"},
])
t2corr.to_csv(OUT / "T2_band_elevation_correlation.csv", index=False)
print(t2corr)

# hypothetical lapse-rate sensitivity
print("T2 lapse ...")
t2 = t.copy()
z = t2["sensor_id"].map(elev)
t2["z_site_m"] = z
t2["adj_term_m"] = (Z_REF - z) / 1000.0  # km below reference station
assert t2["z_site_m"].notna().all(), "unmapped sensor elevation: %s" % (
    sorted(set(t2.loc[t2['z_site_m'].isna(), 'sensor_id'])),)
rows = []
for gamma in [0.0, 5.0, 6.5, 9.8]:
    t2[f"dT_g{gamma}"] = t2["delta_T_K"] - gamma * t2["adj_term_m"]
    for level in ["overall", "band", "season"]:
        keys = [] if level == "overall" else ([level] if level == "band" else ["season"])
        g_eq = eq_dev(t2, f"dT_g{gamma}", keys)
        g_rw = row_wt(t2, f"dT_g{gamma}", keys)
        joined = g_eq.join(g_rw)
        for key, row in joined.iterrows():
            k = key if isinstance(key, str) else str(key)
            rows.append({"gamma_K_per_km": gamma, "level": level, "stratum": k,
                         "mean_dT_adj_equal_device_K": float(row["eq_mean"]),
                         "n_devices": int(row["n_devices"]),
                         "mean_dT_adj_row_weighted_K": float(row["row_mean"]),
                         "n_rows": int(row["n_rows"]),
                         "units": "K"})
t2l = pd.DataFrame(rows)
t2l.to_csv(OUT / "T2_LAPSE_HYPOTHETICAL-SENSITIVITY-NOT-MEASURED-CORRECTION.csv", index=False)
print(t2l[t2l["level"] != "season"].to_string())

# ---------------------------------------------------------------- T3
print("T3 ...")
n_gt100_s = int(ed["rh_gt100_sensor"].sum())
n_gt100_r = int(ed["rh_gt100_ref"].sum())
n_either = int((ed["rh_gt100_sensor"] | ed["rh_gt100_ref"]).sum())
per_dev = ed.groupby("sensor_id").agg(
    n_rows=("rh_gt100_sensor", "size"),
    n_gt100_sensor=("rh_gt100_sensor", "sum"),
    n_gt100_ref=("rh_gt100_ref", "sum"))
per_dev["n_gt100_either"] = ed.groupby("sensor_id").apply(
    lambda g: int((g["rh_gt100_sensor"] | g["rh_gt100_ref"]).sum()), include_groups=False)
per_dev["share_gt100_sensor_pct"] = 100.0 * per_dev["n_gt100_sensor"] / per_dev["n_rows"]
per_dev["share_gt100_either_pct"] = 100.0 * per_dev["n_gt100_either"] / per_dev["n_rows"]
per_dev["serial"] = per_dev.index.map(serial_meta)
per_dev["band"] = t.groupby("sensor_id")["band"].first().reindex(per_dev.index).astype(str).values
per_dev = per_dev.sort_values("n_gt100_sensor", ascending=False)
per_dev.to_csv(OUT / "T3_rh_gt100_per_device.csv")
top5 = per_dev.head(5)
print(top5)
summary["T3"] = {
    "denominator_rows_DERIVED": n_ed,
    "n_rh_gt100_sensor": n_gt100_s,
    "share_rh_gt100_sensor_pct": 100.0 * n_gt100_s / n_ed,
    "n_rh_gt100_ref": n_gt100_r,
    "n_rh_gt100_either": n_either,
    "n_rh_gt100_both_sides": int(n_gt100_s + n_gt100_r - n_either),
    "manuscript_claim": {"numerator": 59091, "denominator": 792036},
    "manuscript_numerator_matches": {"sensor_side_only": bool(n_gt100_s == 59091),
                                     "either_side": bool(n_either == 59091),
                                     "ref_side_only": bool(n_gt100_r == 59091)},
    "manuscript_confirmed": bool(n_either == 59091 and n_ed == 792036),
    "manuscript_share_pct": round(100.0 * n_either / n_ed, 2),
    "numerator_definition": "rows with RH>100 on EITHER sensor or reference side "
                            "(sensor-side alone is 58945; ref-side alone 310; both-sides 164)",
    "top5_devices_by_count": [
        {"sensor_id": int(i), "serial": str(r["serial"]), "band": str(r["band"]),
         "n_rows": int(r["n_rows"]), "n_gt100_sensor": int(r["n_gt100_sensor"]),
         "share_pct": float(r["share_gt100_sensor_pct"])}
        for i, r in top5.iterrows()],
}

# ---------------------------------------------------------------- T4
print("T4 ...")
def tbin(x):
    return pd.cut(x, bins=[-np.inf, 10.0, 20.0, np.inf],
                  labels=["<10C", "10-20C", ">20C"], right=False)

t4_rows = []
for src, val, label in [(t, "delta_T_K", "dT_K"), (e, "delta_e_Pa", "de_Pa")]:
    src = src.assign(ref_bin=tbin(src["t_ref_degC"])) if "t_ref_degC" in src else src
    g_eq = eq_dev(src, val, ["band", "ref_bin"]).reset_index()
    g_rw = row_wt(src, val, ["band", "ref_bin"]).reset_index()
    m = g_eq.merge(g_rw, on=["band", "ref_bin"])
    m["variable"] = label
    m["units"] = "K" if label == "dT_K" else "Pa"
    t4_rows.append(m)
t4 = pd.concat(t4_rows, ignore_index=True)[
    ["variable", "units", "band", "ref_bin", "eq_mean", "n_devices", "row_mean", "n_rows"]]
t4.columns = ["variable", "units", "band", "ref_temp_bin", "mean_equal_device",
              "n_devices", "mean_row_weighted", "n_rows"]
t4.to_csv(OUT / "T4_ref_temperature_bins.csv", index=False)
print(t4.to_string())

# ---------------------------------------------------------------- T5 figure
print("T5 figure ...")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OKABE = {"DJF": "#0072B2", "MAM": "#E69F00", "JJA": "#009E73", "SON": "#CC79A7"}
fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True, sharey=True, dpi=150)
for ax, b in zip(axes.ravel(), BANDS):
    sub = hs[hs["band"] == b]
    for s in SEASONS:
        ss = sub[sub["season"] == s].sort_values("hour")
        ax.plot(ss["hour"], ss["eq_mean"], marker="o", ms=3, lw=1.4, color=OKABE[s], label=s)
    ax.axhline(0, color="0.6", lw=0.8, ls="--")
    ax.set_title(f"{b} band", fontsize=11)
    ax.set_xticks(range(0, 24, 3))
    ax.grid(alpha=0.3, lw=0.5)
axes[0, 0].set_ylabel("Mean ΔT (K), equal-device")
axes[1, 0].set_ylabel("Mean ΔT (K), equal-device")
for ax in axes[1]:
    ax.set_xlabel("Hour of day (UTC)")
axes[0, 0].legend(ncol=2, fontsize=8, frameon=False, title="Season")
fig.suptitle("Hourly ΔT (sensor − Bingley) by hour-of-day and season, per distance band\n"
             "Qualified cohort V2 (frozen); equal-device weighting", fontsize=11)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(OUT / "T5_dT_hour_season_band_small_multiples.png", dpi=150)
plt.close(fig)
print("figure written")

# ---------------------------------------------------------------- spot-check (second code path)
print("spot check ...")
# path 2: pure-python accumulation of per-device means, overall equal-device dT
acc = {}
for sid, val in zip(t["sensor_id"].to_numpy(), t["delta_T_K"].to_numpy()):
    a = acc.setdefault(sid, [0, 0.0])
    a[0] += 1
    a[1] += val
dev_means = [s / n for n, s in acc.values()]
ov2 = float(np.mean(dev_means))
ov1 = float(ov_eq["eq_mean"])
summary["spot_check"]["overall_eq_dT_path1_pandas_groupby_K"] = ov1
summary["spot_check"]["overall_eq_dT_path2_pure_python_K"] = ov2
summary["spot_check"]["abs_difference_K"] = abs(ov1 - ov2)
summary["spot_check"]["agree_within_1e-9"] = bool(abs(ov1 - ov2) < 1e-9)
print(f"spot check: path1={ov1:.9f} path2={ov2:.9f} diff={abs(ov1-ov2):.3e}")

# ---------------------------------------------------------------- SUMMARY.json
summary["T2"] = {
    "z_ref_m": Z_REF,
    "z_ref_source": str(BINGLEY_MIDAS),
    "z_ref_evidence": "MIDAS prolog 'height,G,262,m' (station bingley-no-2, midas_station_id 00513)",
    "band_elevation_stats_file": "T2_band_elevation_stats.csv",
    "band_elevation_mean_m": {b: float(el.loc[b, "mean_m"]) for b in BANDS},
    "band_elevation_sd_m": {b: float(el.loc[b, "sd_m"]) for b in BANDS},
    "corr": {"elevation_vs_band_rank_r": r_elev_rank,
             "mean_dT_vs_elevation_r": r_dT_elev,
             "point_biserial_centre": r_pb_centre,
             "point_biserial_rural": r_pb_rural, "n_devices": int(len(dev))},
    "lapse_file": "T2_LAPSE_HYPOTHETICAL-SENSITIVITY-NOT-MEASURED-CORRECTION.csv",
    "label": "HYPOTHETICAL-SENSITIVITY-NOT-MEASURED-CORRECTION",
    "overall_adj_equal_device_K": {
        str(g): float(t2l[(t2l["level"] == "overall") &
                          (t2l["gamma_K_per_km"] == g)]["mean_dT_adj_equal_device_K"].iloc[0])
        for g in [0.0, 5.0, 6.5, 9.8]},
    "JJA_overall_adj_equal_device_K": {
        str(g): float(t2l[(t2l["level"] == "season") & (t2l["stratum"] == "JJA") &
                          (t2l["gamma_K_per_km"] == g)]["mean_dT_adj_equal_device_K"].iloc[0])
        for g in [0.0, 5.0, 6.5, 9.8]},
}
summary["T4"] = {
    "bins": ["<10C", "10-20C", ">20C"], "descriptive_only": True,
    "file": "T4_ref_temperature_bins.csv",
}
summary["T5"] = {"file": "T5_dT_hour_season_band_small_multiples.png", "dpi": 150,
                 "palette": "Okabe-Ito (colourblind-safe)", "panels": BANDS}
summary["method"] = {
    "equal_device": "mean within device within stratum, then unweighted mean across devices",
    "row_weighted": "plain mean over rows (also reported)",
    "season": "meteorological (DJF/MAM/JJA/SON), UTC timestamps",
    "band_defs": "Centre <=1.5 km, Inner 1.5-4, Outer 4-6.5, Rural >6.5 (bufferkm; band column "
                 "in frozen cohort files)",
    "sign_convention": "observed_minus_reference",
}
with open(OUT / "SUMMARY.json", "w") as f:
    json.dump(summary, f, indent=2)
print("SUMMARY.json written")
