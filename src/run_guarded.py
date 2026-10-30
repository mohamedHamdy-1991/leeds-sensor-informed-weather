#!/opt/homebrew/bin/python3
"""Shared guarded EnergyPlus runner for the Gate-A corrected cases (R1/R2/R3).

Resource guard (REPAIRED per GATE_B_BOUNDED_COMPLETION_ORDER §2, 2026-10-27):
  PRIOR LIMITATION — the previous guard computed vm_stat free+inactive+purgeable
  RAM and mislabelled it "internal_free_GB". Those historical values are RAM
  evidence, NOT disk measurements; existing RUN_RECORD/RESOURCE_GUARD.json files
  keep their original bytes and must never be read as historical disk data.
  The repaired guard measures the ACTUAL internal data volume at /Users/mohamedali
  with shutil.disk_usage (path/device/bytes/GiB recorded), records the external
  output volume separately with a per-job footprint reservation (basis: median
  bytes of prior completed run dirs — no invented universal threshold), keeps
  RAM/memory-pressure evidence under separately named 'ram' fields CONSUMED
  against a stated acceptable level, parses swap robustly (M/G/T/K units, comma
  decimals), and REFUSES whenever any reading is missing/unparseable (the old
  crash/`-1`-passes fallback is removed). Missing swap/pressure/disk -> REFUSE.
Status parsing (order §3): severity labels use whitespace-tolerant ANCHORED
  case-insensitive regex (the literal 'starts-with "** Fatal  **"' missed real
  lines like '   **  Fatal  ** AirflowNetwork: SOLVER ...'); Final-Error-Summary
  block lines ('************* ...') are counted SEPARATELY as summary mentions,
  never as new in-run events.
Writes RUN_RECORD.json + RESOURCE_GUARD.json into the case directory.
Single E+ 23.1.0 binary, sha256-pinned; weather sha256-pinned.

All readbacks are injectable via guard(readbacks=...) so unit/negative tests
exercise every refusal path without launching the engine (synthetic only).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

EPLUS = (os.environ.get("ENERGYPLUS_ENGINE", "energyplus")
         "Work/Ph.D/Publications/Sensor-Calibrated/Publication_Workflow_NEW/"
         "09_TM59/simulation_correction_2026-09-14/tools/EnergyPlus-23.1.0/energyplus-23.1.0")
EPLUS_SHA = "9832298eb181205d"
# Explicit weather dispatch: NO hard-coded default. The caller must supply the exact
# EPW path and its full SHA-256; a wrong-weather launch must be impossible.
WEATHER = os.environ.get("ZC_EPW_PATH") or None
if not WEATHER:
    raise SystemExit("REFUSE: ZC_EPW_PATH not set — this launcher requires an explicit EPW")
WEATHER_SHA = os.environ.get("ZC_EPW_SHA256") or None
if not WEATHER_SHA:
    raise SystemExit("REFUSE: ZC_EPW_SHA256 not set — explicit SHA required for weather identity")
if not os.path.isfile(WEATHER):
    raise SystemExit(f"REFUSE: EPW path does not exist: {WEATHER}")
WEATHER_SHA = "ab17979f299a6517"

INTERNAL_HOME = "/Users/mohamedali"
INTERNAL_FREE_GE_GIB = 2.0
SWAP_USED_LT_MIB = 8192.0
RAM_FREE_PCT_GE = 10.0           # acceptable memory-pressure level (consumed)
EXTERNAL_FREE_JOB_FOOTPRINTS = 2 # output volume must cover this many concurrent jobs


def sha256_head(path, n=16):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def disk_readback(path):
    st = os.stat(path)
    u = shutil.disk_usage(path)
    return {"path": path, "device": st.st_dev,
            "free_bytes": u.free, "total_bytes": u.total,
            "free_GiB": round(u.free / (1024 ** 3), 3)}


def parse_swap_used_mib(text):
    """USED swap from `sysctl vm.swapusage`; robust to M/G/T/K units and commas.
    Returns None when unparseable (caller must REFUSE)."""
    m = re.search(r"used\s*=\s*([0-9]+(?:[.,][0-9]+)?)\s*([kKmMgGtT])", text or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    mult = {"k": 1.0 / 1024, "m": 1.0, "g": 1024.0, "t": 1024.0 ** 2}[m.group(2).lower()]
    return val * mult


def parse_pressure_pct(text):
    m = re.search(r"memory free percentage:\s*([0-9]+(?:[.,][0-9]+)?)\s*%", text or "", re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_vm_free_gb(text):
    m = re.search(r"page size of (\d+)", text or "")
    if not m:
        return None
    ps = int(m.group(1))
    tot = 0
    for key in ("Pages free", "Pages inactive", "Pages purgeable"):
        km = re.search(key + r":\s+(\d+)", text)
        if not km:
            return None
        tot += int(km.group(1)) * ps
    return tot / (1024 ** 3)


def estimate_job_footprint_bytes(runs_root):
    """Per-job output footprint basis: MEDIAN total bytes of prior completed run
    dirs (dirs holding a RUN_RECORD.json marked COMPLETED). Returns
    (bytes, basis_text) or (None, reason) — the caller then REFUSES."""
    sizes = []
    if os.path.isdir(runs_root):
        for root, dirs, files in os.walk(runs_root):
            if "RUN_RECORD.json" not in files:
                continue
            try:
                rec = json.load(open(os.path.join(root, "RUN_RECORD.json")))
            except Exception:
                continue
            if rec.get("status") != "COMPLETED":
                continue
            total = sum(os.path.getsize(os.path.join(root, f)) for f in files)
            if total > 0:
                sizes.append(total)
    if not sizes:
        return None, f"no prior completed run records measurable under {runs_root}"
    sizes.sort()
    med = sizes[len(sizes) // 2]
    return med, (f"median bytes of {len(sizes)} prior completed run dirs under "
                 f"{runs_root} (min {sizes[0]}, max {sizes[-1]})")


SEV_RES = {
    "severe": re.compile(r"^\s*\*\*\s*Severe\s*\*\*", re.I),
    "fatal": re.compile(r"^\s*\*\*\s*Fatal\s*\*\*", re.I),
    "warning": re.compile(r"^\s*\*\*\s*Warning\s*\*\*", re.I),
}
SUMMARY_LINE_RE = re.compile(r"^\s*\*{5,}")


def count_severity_lines(err_text):
    """({kind: in_run_count}, {kind: summary_section_mentions}) — summary-block
    lines are preserved separately and never double-counted as new events."""
    in_run, summary = {k: 0 for k in SEV_RES}, {k: 0 for k in SEV_RES}
    for line in (err_text or "").splitlines():
        for kind, rx in SEV_RES.items():
            if rx.match(line):
                if SUMMARY_LINE_RE.match(line):
                    summary[kind] += 1
                else:
                    in_run[kind] += 1
                break
    return in_run, summary


def guard(readbacks=None, external_runs_root=None):
    rb = {
        "internal_disk": lambda: disk_readback(INTERNAL_HOME),
        "external_disk": lambda: disk_readback(INTERNAL_HOME),  # overridden by caller
        "swap_text": lambda: subprocess.run(["sysctl", "vm.swapusage"],
                                            capture_output=True, text=True).stdout,
        "pressure_text": lambda: subprocess.run(["memory_pressure", "-Q"],
                                                capture_output=True, text=True).stdout,
        "vm_stat_text": lambda: subprocess.run(["vm_stat"],
                                               capture_output=True, text=True).stdout,
    }
    if readbacks:
        rb.update(readbacks)
    refusals, failures = [], []
    internal = external = swap = ram = None

    try:
        d = rb["internal_disk"]()
        if not isinstance(d, dict) or d.get("free_bytes") is None:
            raise ValueError("unreadable internal disk readback")
        d = dict(d, free_GiB=round(d["free_bytes"] / (1024 ** 3), 3),
                 threshold_GiB=INTERNAL_FREE_GE_GIB)
        internal = d
        if d["free_GiB"] < INTERNAL_FREE_GE_GIB:
            failures.append(f"internal data volume free {d['free_GiB']} GiB < "
                            f"{INTERNAL_FREE_GE_GIB} GiB at {d['path']}")
    except Exception as exc:
        refusals.append(f"internal disk reading missing/unparseable: {exc}")

    try:
        d = rb["external_disk"]()
        if not isinstance(d, dict) or d.get("free_bytes") is None:
            raise ValueError("unreadable external disk readback")
        d = dict(d, free_GiB=round(d["free_bytes"] / (1024 ** 3), 3))
        history_root = (external_runs_root or os.environ.get("ZC_RUN_HISTORY_ROOT"))
        if not history_root:
            raise SystemExit("REFUSE: ZC_RUN_HISTORY_ROOT (completed-run history on the "
                             "output SSD) is required — dirname(EPW) is not a run history")
        est, basis = estimate_job_footprint_bytes(history_root)
        if est is None:
            refusals.append(f"per-job output footprint basis unavailable: {basis}")
        else:
            d.update(per_job_footprint_bytes=est, footprint_basis=basis,
                     reserved_bytes=est * EXTERNAL_FREE_JOB_FOOTPRINTS,
                     max_concurrent_jobs=EXTERNAL_FREE_JOB_FOOTPRINTS)
            if d["free_bytes"] < est * EXTERNAL_FREE_JOB_FOOTPRINTS:
                failures.append(
                    f"output volume free {d['free_GiB']} GiB < reserved "
                    f"{est * EXTERNAL_FREE_JOB_FOOTPRINTS / 1024 ** 3:.3f} GiB "
                    f"({EXTERNAL_FREE_JOB_FOOTPRINTS} x per-job footprint; {basis})")
        external = d
    except Exception as exc:
        refusals.append(f"external output-volume reading missing/unparseable: {exc}")

    try:
        raw = rb["swap_text"]()
        mib = parse_swap_used_mib(raw)
        if mib is None:
            raise ValueError(f"cannot parse used swap from {raw!r}")
        swap = {"used_MiB": round(mib, 2), "raw": (raw or "").strip()[:200],
                "threshold_lt_MiB": SWAP_USED_LT_MIB}
        if not mib < SWAP_USED_LT_MIB:
            failures.append(f"swap used {mib:.2f} MiB >= {SWAP_USED_LT_MIB} MiB")
    except Exception as exc:
        refusals.append(f"swap reading missing/unparseable: {exc}")

    try:
        ptxt = rb["pressure_text"]()
        pct = parse_pressure_pct(ptxt)
        vgb = parse_vm_free_gb(rb["vm_stat_text"]())
        if pct is None:
            raise ValueError(f"cannot parse memory free percentage from {ptxt[:120]!r}")
        if vgb is None:
            raise ValueError("cannot parse vm_stat free/inactive/purgeable pages")
        ram = {"memory_free_percentage": pct, "acceptable_free_pct_ge": RAM_FREE_PCT_GE,
               "vm_stat_free_inactive_purgeable_GB": round(vgb, 2),
               "note": "RAM evidence ONLY (separately named; NOT a disk measurement)"}
        if pct < RAM_FREE_PCT_GE:
            failures.append(f"memory free percentage {pct}% < acceptable {RAM_FREE_PCT_GE}%")
    except Exception as exc:
        refusals.append(f"memory-pressure reading missing/unparseable: {exc}")

    decision = "REFUSE" if refusals else ("FAIL" if failures else "PASS")
    record = {"decision": decision, "refusals": refusals, "failures": failures,
              "internal_disk": internal, "external_output_volume": external,
              "swap": swap, "ram": ram,
              "thresholds": {"internal_free_ge_GiB": INTERNAL_FREE_GE_GIB,
                             "swap_used_lt_MiB": SWAP_USED_LT_MIB,
                             "ram_free_pct_ge": RAM_FREE_PCT_GE,
                             "external_free_job_footprints": EXTERNAL_FREE_JOB_FOOTPRINTS}}
    return record, decision == "PASS"


def run_case(case_dir, idf_path, label):
    # external output volume = the volume holding the case directory
    ext_readback = lambda: disk_readback(case_dir)  # noqa: E731
    g, ok = guard(readbacks={"external_disk": ext_readback})
    with open(os.path.join(case_dir, "RESOURCE_GUARD.json"), "w") as f:
        json.dump(g, f, indent=1)
    if not ok:
        print("GUARD REFUSAL — not launching", json.dumps(g))
        sys.exit(2)

    out_dir = os.path.join(case_dir, "OUTPUT")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [EPLUS, "-w", WEATHER, "-d", out_dir, idf_path]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    wall = round(time.time() - t0, 1)

    end_path = os.path.join(out_dir, "eplusout.end")
    end_txt = open(end_path).read().strip() if os.path.exists(end_path) else "(missing)"
    err_path = os.path.join(out_dir, "eplusout.err")
    err_txt = open(err_path, encoding="utf-8", errors="replace").read() if os.path.exists(err_path) else ""
    in_run, summary_mentions = count_severity_lines(err_txt)
    n_sev, n_fatal, n_warn = in_run["severe"], in_run["fatal"], in_run["warning"]
    completed = "EnergyPlus Completed Successfully" in end_txt
    disposition = ("COMPLETED" if (proc.returncode == 0 and completed and not n_sev and not n_fatal)
                   else ("FATAL_TERMINATION" if n_fatal else "FAILED"))

    rec = {
        "case": label,
        "status": disposition,
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "wall_seconds": wall,
        "end_file": end_txt,
        "completed_successfully": completed,
        "warnings": n_warn,
        "severe": n_sev,
        "fatal": n_fatal,
        "summary_section_mentions": summary_mentions,
        "failed_disposition": None if disposition == "COMPLETED" else disposition,
        "idf_sha256_16": sha256_head(idf_path),
        "idf_full_sha256": None,
        "binary_sha256_16": sha256_head(EPLUS),
        "weather_sha256_16": sha256_head(WEATHER),
        "guard": g,
        "sql_present": os.path.exists(os.path.join(out_dir, "eplusout.sql")),
    }
    h = hashlib.sha256()
    with open(idf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    rec["idf_full_sha256"] = h.hexdigest()
    with open(os.path.join(case_dir, "RUN_RECORD.json"), "w") as f:
        json.dump(rec, f, indent=1)
    print(json.dumps({k: rec[k] for k in
                      ("case", "status", "exit_code", "wall_seconds", "end_file",
                       "severe", "fatal", "idf_sha256_16", "idf_full_sha256")}, indent=1))
    if rec["status"] != "COMPLETED":
        sys.exit(3)


if __name__ == "__main__":
    run_case(sys.argv[1], sys.argv[2], sys.argv[3])
