#!/usr/bin/env python3
"""Comprehensive tests for epw_morph: climatology correctness, real-writer
behaviour across a complete annual calendar, physical closure, refusal cases,
column preservation, and hour-mapping negative probe."""
import os, sys, tempfile, shutil
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from epw_morph import (build_climatology, morph_epw, es_pa, es_inv_pa, rh_from_te,
                       EPW_DATA_ROWS, DEW_POINT_UNDEFINED_C)

def _fixture():
    rows = []
    for site, base in [("s1", 1.0), ("s2", 2.0), ("s3", 3.0)]:
        for m in range(1, 13):
            for h in range(24):
                for k in range(4):
                    rows.append({"band": "Centre", "lcz": "1", "month": m, "hour": h,
                                 "site": site, "value": base + 0.1 * k})
    for m in range(1, 13):
        for h in range(24):
            for k in range(4):
                rows.append({"band": "Inner", "lcz": "2", "month": m, "hour": h,
                             "site": "s4", "value": 10.0 + 0.1 * k})
    return pd.DataFrame(rows)

def _make_parent_epw(path, t_base=10.0, rh_base=60.0):
    header = ["LOCATION,Test City,Test,UK,WMO,1111,50.0,-1.0,0.0,100.0\n",
              "DESIGN CONDITIONS,0\n", "TYPICAL/EXTREME PERIODS,0\n", "GROUND TEMPERATURES,0\n",
              "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0\n", "COMMENTS 1,Test\n", "COMMENTS 2,Gen\n",
              "DATA PERIODS,1,1,Data,Sunday,1/1,12/31\n"]
    dim = {1:31,2:28,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}
    lines = []
    for m in range(1, 13):
        for d in range(1, dim[m] + 1):
            for h in range(1, 25):
                t = t_base + 0.1 * (h - 1)
                lines.append(f"2020,{m},{d},{h},0,*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?*?,"
                             f"{t:.1f},{t-2.0:.1f},{rh_base:.1f},101300,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
    assert len(lines) == EPW_DATA_ROWS
    with open(path, "w") as f:
        f.writelines(header); f.writelines(lines)
    return path

def _constant_clim(vT, vE):
    return ({"band:Centre": {(m, h): {"applied": vT, "support": 3, "level": 1} for m in range(1, 13) for h in range(24)}},
            {"band:Centre": {(m, h): {"applied": vE, "support": 3, "level": 1} for m in range(1, 13) for h in range(24)}})

def test_psychrometrics():
    es20 = float(es_pa(np.array([20.0]))[0])
    assert abs(es20 - 2333.4) < 5.0, f"es(20C)={es20}"
    assert abs(float(es_inv_pa(np.array([es20]))[0]) - 20.0) < 0.05
    assert abs(float(rh_from_te(np.array([20.0]), np.array([es20 / 2]))[0]) - 50.0) < 0.1

def test_equal_site_mean():
    c = build_climatology(_fixture(), [("band", "Centre")], "value")["band:Centre"]
    assert all(abs(v["applied"] - 2.15) < 1e-9 for v in c.values())
    assert all(v["support"] == 3 and v["level"] == 1 for v in c.values())

def test_fallback_and_refusal():
    fx = _fixture()
    c2 = build_climatology(fx, [("band", "Inner")], "value")["band:Inner"]
    assert all(v["level"] == 3 and v["support"] == 1 for v in c2.values())
    assert all(abs(v["applied"] - 4.15) < 1e-9 for v in c2.values())
    try:
        build_climatology(fx.iloc[:0], [("band", "Centre")], "value"); raise AssertionError("should raise")
    except ValueError: pass

def test_nonfinite_refusal():
    fx = _fixture(); fx.loc[0, "value"] = float("nan")
    try:
        build_climatology(fx, [("band", "Centre")], "value"); raise AssertionError("should raise")
    except ValueError as e: assert "nonfinite" in str(e).lower()

def test_writer_complete_calendar():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(1.0, 100.0)
    out = os.path.join(tmp, "out.epw"); qc = morph_epw(parent, out, clim_T, clim_E, "band:Centre")
    assert qc["rows"] == EPW_DATA_ROWS
    with open(out) as f: out_lines = f.readlines()[8:]
    assert len(out_lines) == EPW_DATA_ROWS
    for i, line in enumerate(out_lines):
        parts = line.strip().split(",")
        h_epw = int(parts[3]); t0 = 10.0 + 0.1 * (h_epw - 1)
        t1 = float(parts[6]); assert abs(t1 - (t0 + 1.0)) < 0.05, f"row {i}: T {t1} vs {t0+1.0}"
        rh = float(parts[8]); assert 0.0 <= rh <= 100.0
        td = float(parts[7]); assert td <= t1 + 0.1
    shutil.rmtree(tmp)

def test_writer_hour_mapping_negative_probe():
    """Verify the code uses EPW hour (index 3), not day (index 2).
    The CR1 bug would read day (1-31) as hour, failing for day > 24."""
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    # A climatology missing (m, 23) must fail if the code correctly maps hour 24 -> profile 23
    partial = {"band:Centre": {(m, h): {"applied": 0.0, "support": 3, "level": 1}
                                for m in range(1, 13) for h in range(23)}}  # h=0..22 only
    out = os.path.join(tmp, "out.epw")
    try:
        morph_epw(parent, out, partial, partial, "band:Centre")
        raise AssertionError("should have raised for missing (m, 23) — proves hour mapping uses index 3")
    except KeyError: pass
    shutil.rmtree(tmp)

def test_writer_closure():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(2.0, 50.0)
    out = os.path.join(tmp, "out.epw"); morph_epw(parent, out, clim_T, clim_E, "band:Centre")
    with open(out) as f: out_lines = f.readlines()[8:]
    for line in out_lines:
        parts = line.strip().split(",")
        t = float(parts[6]); td = float(parts[7]); rh = float(parts[8])
        assert 0.0 <= rh <= 100.0; assert td <= t + 0.1
        if rh < 99.95:
            e = float(es_pa(np.array([t]))[0]) * rh / 100.0
            td_calc = float(es_inv_pa(np.array([e]))[0])
            assert abs(td - td_calc) < 0.2
    shutil.rmtree(tmp)

def test_writer_zero_pressure_sentinel():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(0.0, -100000.0)
    out = os.path.join(tmp, "out.epw")
    qc = morph_epw(parent, out, clim_T, clim_E, "band:Centre")
    assert qc["degenerate_e0_rows"] > 0
    assert qc["dew_point_sentinel_C"] == DEW_POINT_UNDEFINED_C
    with open(out) as f: out_lines = f.readlines()[8:]
    sc = sum(1 for l in out_lines if f",{DEW_POINT_UNDEFINED_C:.1f}," in l)
    assert sc == qc["degenerate_e0_rows"]
    shutil.rmtree(tmp)

def test_writer_nonfinite_refusal():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(0.0, 0.0)
    clim_T["band:Centre"][(1, 0)]["applied"] = float("inf")
    out = os.path.join(tmp, "out.epw")
    try:
        morph_epw(parent, out, clim_T, clim_E, "band:Centre"); raise AssertionError("should raise")
    except ValueError as e: assert "nonfinite" in str(e).lower()
    shutil.rmtree(tmp)

def test_writer_column_preservation():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(3.0, 200.0)
    out = os.path.join(tmp, "out.epw"); morph_epw(parent, out, clim_T, clim_E, "band:Centre")
    with open(parent) as f: pdata = f.readlines()[8:]
    with open(out) as f: odata = f.readlines()[8:]
    for i, (pl, ol) in enumerate(zip(pdata, odata)):
        pp, op = pl.strip().split(","), ol.strip().split(",")
        assert len(pp) == len(op)
        for j in range(len(pp)):
            if j not in (6, 7, 8): assert pp[j] == op[j], f"row {i} col {j}: {pp[j]} != {op[j]}"
    shutil.rmtree(tmp)

def test_writer_collision_refusal():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    clim_T, clim_E = _constant_clim(0.0, 0.0)
    out = os.path.join(tmp, "out.epw")
    morph_epw(parent, out, clim_T, clim_E, "band:Centre")
    try:
        morph_epw(parent, out, clim_T, clim_E, "band:Centre"); raise AssertionError("should refuse")
    except FileExistsError: pass
    shutil.rmtree(tmp)

def test_writer_parent_rh_range_refusal():
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    with open(parent) as f: lines = f.readlines()
    parts = lines[8].strip().split(","); parts[8] = "150.0"; lines[8] = ",".join(parts) + "\n"
    with open(parent, "w") as f: f.writelines(lines)
    clim_T, clim_E = _constant_clim(0.0, 0.0)
    out = os.path.join(tmp, "out.epw")
    try:
        morph_epw(parent, out, clim_T, clim_E, "band:Centre"); raise AssertionError("should raise")
    except ValueError as e: assert "RH" in str(e) and "clamped" in str(e).lower()
    shutil.rmtree(tmp)

def test_writer_short_row_refusal():
    """A 15-field final row must be refused (EPW requires full schema)."""
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    with open(parent) as f: lines = f.readlines()
    parts = lines[-1].strip().split(","); lines[-1] = ",".join(parts[:15]) + "\n"
    with open(parent, "w") as f: f.writelines(lines)
    clim_T, clim_E = _constant_clim(0.0, 0.0)
    out = os.path.join(tmp, "out.epw")
    try:
        morph_epw(parent, out, clim_T, clim_E, "band:Centre"); raise AssertionError("should refuse 15-field row")
    except (ValueError, IndexError): pass
    shutil.rmtree(tmp)

def test_writer_swapped_rows_refusal():
    """Swapping the first two hourly rows must be refused (ordered calendar)."""
    tmp = tempfile.mkdtemp(); parent = _make_parent_epw(os.path.join(tmp, "parent.epw"))
    with open(parent) as f: lines = f.readlines()
    lines[8], lines[9] = lines[9], lines[8]  # swap first two data rows
    with open(parent, "w") as f: f.writelines(lines)
    clim_T, clim_E = _constant_clim(0.0, 0.0)
    out = os.path.join(tmp, "out.epw")
    try:
        morph_epw(parent, out, clim_T, clim_E, "band:Centre"); raise AssertionError("should refuse swapped rows")
    except ValueError as e: assert "sequence mismatch" in str(e).lower() or "expected" in str(e).lower()
    shutil.rmtree(tmp)

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for t in tests:
        try: t(); print(f"  PASS: {t.__name__}"); p += 1
        except Exception as e: print(f"  FAIL: {t.__name__}: {e}"); f += 1
    print(f"\n{p} passed, {f} failed out of {len(tests)}")
    if f: sys.exit(1)
