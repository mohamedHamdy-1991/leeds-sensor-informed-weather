#!/opt/homebrew/bin/python3
"""Shared TM59 + hourly extraction from an EnergyPlus SQLite output (stage-1 method).

Replicates the sealed stage-1 conventions exactly (verified against
03_RESULTS_SEALED values byte-for-byte before use):
  - TM59 metrics  = ReportData 'Run Period' single aggregated value per EMS variable
  - Hourly operative = 'Zone Operative Temperature' Hourly per zone (8760 rows)
Writes TM59_RESULTS.csv + HOURLY_ZONE_RESULTS.csv into the destination.
"""
import csv
import os
import sqlite3
import sys


def extract(sql_path, out_dir, env_index=None):
    con = sqlite3.connect(sql_path)
    cur = con.cursor()
    if env_index is None:
        rows = cur.execute("SELECT EnvironmentPeriodIndex FROM EnvironmentPeriods "
                           "WHERE EnvironmentType=3").fetchall()
        env_index = rows[0][0]

    d = {}
    for idx, name, freq, units in cur.execute(
            "SELECT ReportDataDictionaryIndex, Name, ReportingFrequency, Units "
            "FROM ReportDataDictionary WHERE Name LIKE '%CIBSE TM59%'").fetchall():
        if freq != "Run Period":
            continue
        vals = cur.execute(
            "SELECT r.Value FROM ReportData r JOIN Time t ON r.TimeIndex=t.TimeIndex "
            "WHERE r.ReportDataDictionaryIndex=? AND t.EnvironmentPeriodIndex=?",
            (idx, env_index)).fetchall()
        if vals:
            d[name] = (vals[0][0], units)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "TM59_RESULTS.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "zone", "value", "unit", "source"])
        for name, (val, units) in sorted(d.items()):
            metric = ("corridor_advisory_pct_gt28C" if " Corridors " in name else
                      "C1_pct_occupied_hours_dT_ge1K_May_Sep" if "Criterion A" in name else
                      "C2_hours_gt26C_22_07_annual" if "Criterion B" in name else
                      "c_mech_vent_pct_gt26C" if "Mech" in name or "MECH" in name else
                      name)
            w.writerow([metric, name, val, units, "native EMS (RunPeriod cumulative)"])

    # hourly operative per zone
    ops = cur.execute(
        "SELECT DISTINCT ReportDataDictionaryIndex, KeyValue FROM ReportDataDictionary "
        "WHERE Name='Zone Operative Temperature' AND ReportingFrequency='Hourly'").fetchall()
    seen = {}
    for idx, key in ops:
        seen.setdefault(key, idx)
    with open(os.path.join(out_dir, "HOURLY_ZONE_RESULTS.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "month", "day", "hour_ending", "operative_temp_C"])
        for key, idx in sorted(seen.items()):
            rows = cur.execute(
                "SELECT t.Month,t.Day,t.Hour,r.Value FROM ReportData r "
                "JOIN Time t ON r.TimeIndex=t.TimeIndex "
                "WHERE r.ReportDataDictionaryIndex=? AND t.EnvironmentPeriodIndex=? "
                "ORDER BY t.TimeIndex", (idx, env_index)).fetchall()
            for m, dd, h, v in rows:
                w.writerow([key, m, dd, h, v])
    con.close()
    return d, sorted(seen)


if __name__ == "__main__":
    d, zones = extract(sys.argv[1], sys.argv[2])
    for k, (v, u) in sorted(d.items()):
        print(f"TM59  {k} = {v} {u}")
    print("hourly zones:", zones)
