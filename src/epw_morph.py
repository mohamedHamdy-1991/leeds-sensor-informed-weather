#!/usr/bin/env python3
"""Reference implementation of the sensor-informed EPW morph used by the Leeds study.

Implements the frozen rules of the scenario-morph contract (v2.2):
  * two-stage equal-site means (per-site first, then unweighted over sites);
  * fallback hierarchy L1 group x month x hour -> L2 group x season x hour ->
    L3 all-site month x hour -> L4 all-site season x hour -> L5 all-site global,
    each requiring >= SUPPORT_MIN_SITES contributing sites;
  * departures applied to parent dry-bulb temperature and to vapour pressure
    derived from parent RH; vapour pressure bounded at saturation of the
    adjusted temperature; RH and dew point reconstructed from the final state;
  * parent wind, radiation and sky-infrared fields untouched.

EPW column convention (0-based): 0=year, 1=month, 2=day, 3=hour (1-24,
end-of-hour), 6=dry-bulb, 7=dew-point, 8=RH. The profile-hour keys in the
climatology are 0-23, so reading maps EPW hour h_epw -> h_profile = h_epw - 1.
When vapour pressure reaches 0 Pa, dew point is written as -70.0 C (the EPW
data-range floor) per the accepted generator convention, not as T.

Dependencies: numpy, pandas. The public entry points are `build_climatology`
(pure) and `morph_epw` (file-level). Units: temperature degC, vapour
pressure Pa, dew point degC, RH %.
"""
from __future__ import annotations

import os
import re
import numpy as np
import pandas as pd

E0_PA, MAGNUS_A, MAGNUS_B = 610.94, 17.625, 243.04
SUPPORT_MIN_SITES = 2
EPW_DATA_ROWS = 8760
EPW_HEADER_LINES = 8
EPW_N_COLS = 32  # standard EPW column count
DEW_POINT_UNDEFINED_C = -70.0  # EPW data-range floor per accepted convention
MONTH_SEASON = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
                6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}


def es_pa(t_c):
    """Magnus/Tetens saturation vapour pressure over water (Pa)."""
    t = np.asarray(t_c, dtype=float)
    return E0_PA * np.exp(MAGNUS_A * t / (t + MAGNUS_B))


def es_inv_pa(e_pa):
    """Inverse Magnus: dew point (degC) from vapour pressure (Pa)."""
    ln = np.log(np.maximum(np.asarray(e_pa, dtype=float), 1e-9) / E0_PA)
    return MAGNUS_B * ln / (MAGNUS_A - ln)


def rh_from_te(t_c, e_pa):
    """RH (%) consistent with (T, e), clipped to [0, 100]."""
    rh = 100.0 * np.asarray(e_pa, dtype=float) / es_pa(t_c)
    return np.clip(rh, 0.0, 100.0)


def _check_finite(name, *vals):
    for v in vals:
        if not np.isfinite(v):
            raise ValueError(f"{name}: nonfinite value {v}")


def build_climatology(paired: pd.DataFrame, groups: list[tuple[str, str]],
                      value_col: str, support_min: int = SUPPORT_MIN_SITES) -> dict:
    """Equal-site month-hour climatology with the frozen fallback hierarchy.

    paired: DataFrame with columns [band, lcz, month (1-12), hour (0-23),
            site, value]; band/lcz carry each row's group labels (as in the
            study's paired cohort files). groups: list of (kind, label) with
            kind in {"band", "lcz"}.
    Returns {f"{kind}:{g}": {(m, h): {"applied": v, "support": n, "level": L}}}.
    Raises on any unscorable cell after the full hierarchy or nonfinite values.
    """
    if paired[value_col].isna().any() or not np.isfinite(paired[value_col].dropna()).all():
        raise ValueError(f"nonfinite values in {value_col} column")
    df = paired.assign(season=paired["month"].map(MONTH_SEASON))

    def agg(index_cols):
        return df.groupby(index_cols, sort=True).agg(s=(value_col, "sum"), n=(value_col, "count"))

    a1 = {k: agg([k, "month", "hour", "site"]) for k in ("band", "lcz")}
    a2 = {k: agg([k, "season", "hour", "site"]) for k in ("band", "lcz")}
    a3, a4, a5 = agg(["month", "hour", "site"]), agg(["season", "hour", "site"]), agg(["site"])
    tables = {1: a1, 2: a2, 3: a3, 4: a4, 5: a5}

    def cell_mean(table, key):
        if len(key) == 0:
            sub = table
        else:
            try:
                sub = table.xs(key, level=tuple(range(len(key))))
            except KeyError:
                return None, 0
        pos = sub[sub["n"] > 0]
        if len(pos) == 0:
            return None, 0
        return float((pos["s"] / pos["n"]).mean()), int(len(pos))

    out: dict = {}
    for kind, g in groups:
        cells = {}
        for m in range(1, 13):
            for h in range(24):
                season = MONTH_SEASON[m]
                n1 = 0
                applied, level, support = None, 6, 0
                for key, lvl in (((g, m, h), 1), ((g, season, h), 2),
                                 ((m, h), 3), ((season, h), 4), ((), 5)):
                    t = tables[lvl][kind] if lvl in (1, 2) else tables[lvl]
                    v, n = cell_mean(t, key)
                    if lvl == 1:
                        n1 = n
                    if v is not None and n >= support_min:
                        applied, level, support = v, lvl, n1
                        break
                if applied is None:
                    raise ValueError(f"unscorable cell {kind}:{g} {(m, h)}")
                cells[(m, h)] = {"applied": applied, "support": n1, "level": level}
        out[f"{kind}:{g}"] = cells
    return out


def morph_epw(parent_path: str, out_path: str, clim_T: dict, clim_E: dict,
              group_key: str) -> dict:
    """Write a conditional EPW variant. Returns a QC summary.

    parent_path: the licensed parent EPW (8 header lines + 8760 rows).
    clim_T / clim_E: build_climatology outputs for temperature and
    vapour-pressure departures; group_key like 'band:Centre'.
    Only EPW columns 7 (dry-bulb, 0-based 6), 8 (dew point) and 9 (RH) change;
    all other columns are byte-preserved from the parent.

    Guards: refuses nonfinite correction values or parent readings; validates
    the EPW column count and row count; validates month/hour ranges from the
    actual data rows; refuses to overwrite an existing output file.

    Sentinel: when the bounded vapour pressure reaches 0 Pa, dew point is
    written as -70.0 C (the EPW data-range floor) per the accepted generator
    convention, not as T.
    """
    if os.path.exists(out_path):
        raise FileExistsError(f"refusing to overwrite existing output: {out_path}")
    with open(parent_path) as f:
        lines = f.readlines()
    if len(lines) < EPW_HEADER_LINES + 1:
        raise ValueError(f"expected at least {EPW_HEADER_LINES + 1} lines, got {len(lines)}")
    header = lines[:EPW_HEADER_LINES]
    data = lines[EPW_HEADER_LINES:]
    if len(data) != EPW_DATA_ROWS:
        raise ValueError(f"expected exactly {EPW_DATA_ROWS} data rows after header, got {len(data)}; "
                         f"extra or missing rows will not be silently discarded")
    # schema check: column count on EVERY row
    for i, line in enumerate(data):
        ncols = len(line.rstrip("\n").split(","))
        if ncols < 15:
            raise ValueError(f"row {i}: only {ncols} columns (expected >= 15); malformed EPW")
    # calendar validation: verify the 8760 rows form a complete, ORDERED,
    # non-leap-year calendar sequence (not just a set-based membership check)
    _DAYS = {1:31,2:28,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}
    expected_sequence = []
    for m in range(1, 13):
        for d in range(1, _DAYS[m] + 1):
            for h in range(1, 25):
                expected_sequence.append((m, d, h))
    for i, line in enumerate(data):
        parts_raw = line.rstrip("\n").split(",")
        if len(parts_raw) < 15:
            raise ValueError(f"row {i}: only {len(parts_raw)} columns (expected >= 15); malformed EPW")
        try:
            m_p, d_p, h_p = int(parts_raw[1]), int(parts_raw[2]), int(parts_raw[3])
        except (ValueError, IndexError):
            raise ValueError(f"row {i}: cannot parse month/day/hour from {parts_raw[:4]}")
        expected = expected_sequence[i]
        actual = (m_p, d_p, h_p)
        if actual != expected:
            raise ValueError(f"row {i}: calendar sequence mismatch: got {actual}, expected {expected} "
                             f"(rows must be in strict chronological order)")
    cT, cE = clim_T[group_key], clim_E[group_key]
    n_bounded = n_rows = 0
    n_degenerate = 0
    out_lines = []
    seen = set()
    for i, line in enumerate(data):
        parts = line.rstrip("\n").split(",")
        year, month, day, hour_epw = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        hour = hour_epw - 1  # EPW 1-24 end-of-hour -> 0-23 profile hour
        if not (1 <= month <= 12):
            raise ValueError(f"row {i}: month {month} out of range")
        if not (0 <= hour <= 23):
            raise ValueError(f"row {i}: hour {hour_epw} -> profile {hour} out of range")
        key = (month, day, hour_epw)
        if key in seen:
            raise ValueError(f"row {i}: duplicate calendar key {key}")
        seen.add(key)
        dT = cT[(month, hour)]["applied"]
        dE = cE[(month, hour)]["applied"]
        _check_finite(f"row {i} dT", dT)
        _check_finite(f"row {i} dE", dE)
        t0_str = parts[6].strip()
        rh0_str = parts[8].strip()
        t0 = float(t0_str)
        rh0 = float(rh0_str)
        _check_finite(f"row {i} parent T", t0)
        _check_finite(f"row {i} parent RH", rh0)
        # compute parent e from actual RH (do NOT silently clamp; the EPW spec
        # has RH in [0,100] and the parent data is a licensed verified product)
        if rh0 < 0.0 or rh0 > 100.0:
            raise ValueError(f"row {i}: parent RH {rh0} outside [0, 100]; parent data issue, not silently clamped")
        e0 = float(es_pa(np.array([t0]))[0]) * rh0 / 100.0
        t1 = t0 + dT
        e1 = max(e0 + dE, 0.0)
        e_sat = float(es_pa(np.array([t1]))[0])
        bounded = e1 > e_sat
        if bounded:
            e1 = e_sat
            n_bounded += 1
        if e_sat <= 0:
            raise ValueError(f"row {i}: saturation pressure {e_sat} <= 0 at T={t1}")
        rh1 = min(100.0 * e1 / e_sat, 100.0)
        if e1 <= 0:
            # dew point undefined at e=0: write EPW data-range floor per accepted convention
            td1 = DEW_POINT_UNDEFINED_C
            n_degenerate += 1
        else:
            td1 = float(es_inv_pa(np.array([e1]))[0])
            td1 = min(td1, t1)
        parts[6] = f"{t1:.1f}"
        parts[7] = f"{td1:.1f}"
        parts[8] = f"{rh1:.1f}"
        out_lines.append(",".join(parts))
        n_rows += 1
    if n_rows != EPW_DATA_ROWS:
        raise ValueError(f"processed {n_rows} rows, expected {EPW_DATA_ROWS}")
    with open(out_path, "w") as f:
        f.writelines(header)
        f.writelines(l + "\n" for l in out_lines)
    return {"rows": n_rows, "bounded_at_saturation": n_bounded,
            "bounded_share": n_bounded / n_rows,
            "degenerate_e0_rows": n_degenerate,
            "dew_point_sentinel_C": DEW_POINT_UNDEFINED_C}
