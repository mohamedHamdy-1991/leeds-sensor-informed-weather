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

Dependencies: numpy, pandas. The public entry point is `build_climatology`
(pure) and `morph_epw` (file-level). Units: temperature degC, vapour
pressure Pa, dew point degC, RH %.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

E0_PA, MAGNUS_A, MAGNUS_B = 610.94, 17.625, 243.04
SUPPORT_MIN_SITES = 2
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


def build_climatology(paired: pd.DataFrame, groups: list[tuple[str, str]],
                      value_col: str, support_min: int = SUPPORT_MIN_SITES) -> dict:
    """Equal-site month-hour climatology with the frozen fallback hierarchy.

    paired: DataFrame with columns [band, lcz, month (1-12), hour (0-23),
            site, value]; band/lcz carry each row's group labels (as in the
            study's paired cohort files). groups: list of (kind, label) with
            kind in {"band", "lcz"}.
    Returns {f"{kind}:{g}": {(m, h): {"applied": v, "support": n, "level": L}}}.
    Raises on any unscorable cell after the full hierarchy.
    """
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
    clim_T / clim_E: build_climatology outputs for the temperature and
    vapour-pressure departures; group_key like 'band:Centre'.
    Only EPW columns 7 (dry-bulb, 0-based 6), 8 (dew point) and 9 (RH) change;
    wind, radiation and sky-infrared are byte-preserved from the parent.
    """
    with open(parent_path) as f:
        lines = f.readlines()
    header, data = lines[:8], lines[8:]
    if len(data) != 8760:
        raise ValueError(f"expected 8760 data rows, got {len(data)}")
    cT, cE = clim_T[group_key], clim_E[group_key]
    n_bounded = n_rows = 0
    out_lines = []
    for i, line in enumerate(data):
        parts = line.rstrip("\n").split(",")
        month, hour = int(parts[1]), int(parts[2])
        dT = cT[(month, hour)]["applied"]
        dE = cE[(month, hour)]["applied"]
        t0 = float(parts[6])
        rh0 = min(max(float(parts[8]), 0.0), 100.0)
        e0 = float(es_pa(np.array([t0]))[0]) * rh0 / 100.0
        t1 = t0 + dT
        e1 = max(e0 + dE, 0.0)
        e_sat = float(es_pa(np.array([t1]))[0])
        bounded = e1 > e_sat
        if bounded:
            e1 = e_sat
            n_bounded += 1
        rh1 = 100.0 * e1 / e_sat if e_sat > 0 else 0.0
        td1 = float(es_inv_pa(np.array([e1]))[0]) if e1 > 0 else t1
        td1 = min(td1, t1)
        parts[6] = f"{t1:.1f}"
        parts[7] = f"{td1:.1f}"
        parts[8] = f"{rh1:.1f}"
        out_lines.append(",".join(parts))
        n_rows += 1
    with open(out_path, "w") as f:
        f.writelines(header)
        f.writelines(l + "\n" for l in out_lines)
    return {"rows": n_rows, "bounded_at_saturation": n_bounded,
            "bounded_share": n_bounded / n_rows}
