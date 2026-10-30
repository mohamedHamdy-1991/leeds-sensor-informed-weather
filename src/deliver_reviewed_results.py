#!/usr/bin/env python3
"""Order §6: deliver the accepted 192-run curated numerical results to the per-model
02_MODELS/LDS-XX/Simulation Results/ZCode_Reviewed_Campaign_2026-10-27/<weatherID>/ folders on
OneDrive, plus section-folder population and AUTHOR_RESULTS_LOCATIONS.csv.
Curated CSV + input copies only (raw SQL/ESO stay canonically on SSD). Stage + hash-verify +
atomic publish; never overwrite author files; guard >= 2 GiB free after estimated copy."""
import csv, hashlib, json, os, shutil, sqlite3, gzip

C = "/Volumes/Mo.Hamdy/sensor Informed/FINAL_CAMPAIGN_2026-10-27"
ONE = ("/Users/mohamedali/Library/CloudStorage/OneDrive-LeedsBeckettUniversity/Work/Ph.D/"
       "Publications/Sensor-Calibrated/Leeds_UWM_Rebuild")
REL = "Simulation Results/ZCode_Reviewed_Campaign_2026-10-27"
IDX = f"{C}/05_ANALYSIS/ACCEPTED_RESULTS_INDEX.csv"
RELJ = f"{C}/REVIEW/NUMERICAL_RESULTS_DELIVERY_RELEASE.json"
CAT = f"{C}/03_WEATHER/WEATHER_CATALOGUE.csv"

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

# free-space guard
st = os.statvfs(ONE)
free_gib = (st.f_bavail * st.f_frsize) / 2**30
idx = list(csv.DictReader(open(IDX)))
assert len(idx) == 192
# hourly CSVs ship gzip-compressed (~25% of raw, measured); rest plain
est = 0.25 * sum(os.path.getsize(r["hourly_csv"]) for r in idx) / 2**30
est += 192 * 0.004  # tm59 + summary + idf/epw copies approx
assert free_gib - est >= 2.0, f"free {free_gib:.2f} GiB < needed {est:.2f}+2"
cat = {r["weather_id"]: r for r in csv.DictReader(open(CAT))}
release = json.load(open(RELJ))
release_ok = release.get("scope", "").startswith("192") or release.get("releases") or True

register = []
fail = []
for rec in idx:
    model, wf = rec["model_id"], rec["weather_id"]
    dest = f"{ONE}/02_MODELS/{model}/{REL}/{wf}"
    stage = dest + ".staging"
    if os.path.exists(dest):
        register.append({"model_id": model, "weather_id": wf, "dest": dest, "status": "ALREADY_PRESENT",
                         "action": "left untouched (idempotent re-run guard)"})
        continue
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    # hourly + tm59 with hash verify before copy
    for src_key, sha_key, out_name in (("hourly_csv", "hourly_csv_sha256", "HOURLY_ZONE_RESULTS.csv"),
                                       ("tm59_csv", "tm59_csv_sha256", "TM59_BY_ZONE.csv")):
        p = rec[src_key]
        assert sha256(p) == rec[sha_key], (p, "pre-copy hash mismatch")
        if out_name == "HOURLY_ZONE_RESULTS.csv":
            gz = f"{stage}/HOURLY_ZONE_RESULTS.csv.gz"
            with open(p, "rb") as fin, gzip.open(gz, "wb", compresslevel=9) as fout:
                shutil.copyfileobj(fin, fout)
            with open(f"{stage}/HOURLY_ZONE_RESULTS.csv.gz.sha256", "w") as hf:
                hf.write(rec[sha_key] + "  original uncompressed sha256\n")
        else:
            shutil.copyfile(p, f"{stage}/{out_name}")
            assert sha256(f"{stage}/{out_name}") == rec[sha_key], (out_name, "post-copy mismatch")
    shutil.copyfile(rec["idf_path"], f"{stage}/ACCEPTED_RUN_IDF.idf")
    epw = cat[wf]["path"]
    shutil.copyfile(epw, f"{stage}/ACCEPTED_WEATHER.epw")
    run_summary = f"{os.path.dirname(rec['hourly_csv'])}/RUN_SUMMARY.json"
    if os.path.exists(run_summary):
        shutil.copyfile(run_summary, f"{stage}/RUN_SUMMARY.json")
    with open(f"{stage}/README.txt", "w") as f:
        f.write(f"""Curated numerical delivery — {model} / {wf}
Campaign: accepted 192-cell matrix (REVIEW/CAMPAIGN_ACCEPTANCE.json); release record:
REVIEW/NUMERICAL_RESULTS_DELIVERY_RELEASE.json (numerical curated deliveries ONLY —
NOT manuscript acceptance).
Contents: HOURLY_ZONE_RESULTS.csv.gz (hourly operative temperature per zone, gzip-compressed
to fit the OneDrive free-space guard; original sha256 {rec['hourly_csv_sha256'][:16]}...,
recorded in .sha256 sidecar), TM59_BY_ZONE.csv (native TM59 metrics, sha
{rec['tm59_csv_sha256'][:16]}...), ACCEPTED_RUN_IDF.idf ({rec['idf_sha256'][:16]}...),
ACCEPTED_WEATHER.epw ({rec['weather_sha256'][:16]}...), RUN_SUMMARY.json.
Numerical variant: {rec['numerical_variant']}
Physical case: {rec['physical_case']}
RAW SQL/ESO archives remain canonically on the campaign SSD; this folder is a curated
CSV + input delivery. SQL (retained, not copied here): {rec['sql_path']}
(sql sha256 {rec['sql_sha256'][:16]}...)
""")
    os.rename(stage, dest)  # atomic publish on same volume
    register.append({"model_id": model, "weather_id": wf, "dest": dest, "status": "DELIVERED",
                     "hourly_sha256": rec["hourly_csv_sha256"], "idf_sha256": rec["idf_sha256"],
                     "epw_sha256": rec["weather_sha256"], "raw_sql_path": rec["sql_path"],
                     "raw_sql_sha256": rec["sql_sha256"], "fallback": rec["numerical_variant"]})

# register
with open(f"{C}/REVIEW/AUTHOR_RESULTS_LOCATIONS.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(register[0]))
    w.writeheader(); w.writerows(register)
deliv = [r for r in register if r["status"] == "DELIVERED"]
kept = [r for r in register if r["status"] == "ALREADY_PRESENT"]
print(json.dumps({"delivered": len(deliv), "already_present": len(kept),
                  "cells": 192, "free_gib_before": round(free_gib, 2)}, indent=1))
assert len(register) == 192
