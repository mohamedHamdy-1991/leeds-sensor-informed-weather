#!/usr/bin/env python3
"""Golden tests for epw_morph: psychrometric constants, two-stage equal-site
means, fallback hierarchy L1->L3, and unscorable-cell refusal (synthetic data)."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from epw_morph import build_climatology, es_pa, es_inv_pa, rh_from_te

def test_psychrometrics():
    es20 = float(es_pa(np.array([20.0]))[0])
    assert abs(es20 - 2333.4) < 5.0
    assert abs(float(es_inv_pa(np.array([es20]))[0]) - 20.0) < 0.05
    assert abs(float(rh_from_te(np.array([20.0]), np.array([es20 / 2]))[0]) - 50.0) < 0.1

def _fixture():
    rows = []
    for site, base in [("s1", 1.0), ("s2", 2.0), ("s3", 3.0)]:
        for m in range(1, 13):
            for h in range(24):
                for k in range(4):
                    rows.append({"band": "Centre", "lcz": "1", "month": m, "hour": h,
                                 "site": site, "value": base + 0.1 * k})
    # a second band with a single site: L1/L2 support = 1 < 2, so resolution
    # must fall through to the all-site pooled levels (L3+), which see 4 sites
    for m in range(1, 13):
        for h in range(24):
            for k in range(4):
                rows.append({"band": "Inner", "lcz": "2", "month": m, "hour": h,
                             "site": "s4", "value": 10.0 + 0.1 * k})
    return pd.DataFrame(rows)

def test_equal_site_mean():
    c = build_climatology(_fixture(), [("band", "Centre")], "value")["band:Centre"]
    assert all(abs(v["applied"] - 2.15) < 1e-9 for v in c.values())
    assert all(v["support"] == 3 and v["level"] == 1 for v in c.values())

def test_fallback_and_refusal():
    fx = _fixture()
    # single-site group: L1/L2 support 1 < 2 -> pooled all-site month-hour (L3, 4 sites)
    c2 = build_climatology(fx, [("band", "Inner")], "value")["band:Inner"]
    assert all(v["level"] == 3 and v["support"] == 1 for v in c2.values())
    assert all(abs(v["applied"] - 4.15) < 1e-9 for v in c2.values())  # pooled all-site mean at that (m,h)
    # empty input: nothing scorable anywhere -> hard refusal
    try:
        build_climatology(fx.iloc[:0], [("band", "Centre")], "value")
        raise AssertionError("should have raised")
    except ValueError:
        pass

if __name__ == "__main__":
    test_psychrometrics(); test_equal_site_mean(); test_fallback_and_refusal()
    print("GOLDEN TESTS PASS")
