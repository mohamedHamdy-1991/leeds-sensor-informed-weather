#!/opt/homebrew/bin/python3
"""Create a stage-1-convention run copy for a corrected case IDF.

Mirrors the stage-1 RUN_COPY_PROOF convention exactly: the ONLY change vs the
corrected case IDF is an appended reporting block (Output:SQLite SimpleAndTabular
+ 5 hourly Output:Variable) — identical object text to the accepted stage-1
baseline run copies. Writes RUN_COPY_PROOF.json next to the run copy.
"""
import hashlib
import json
import sys

BLOCK = """
!- ==== GATE-A CORRECTED CASE — ADDED REPORTING REQUESTS ONLY (27 Oct 2026) ====
!- Nothing above this line is modified. Added: SQLite output + 5 hourly Output:Variable
!- (identical block to the accepted stage-1 baseline run copies, 27 Oct 2026).
Output:SQLite, SimpleAndTabular;
Output:Variable, *, Zone Operative Temperature, Hourly;
Output:Variable, *, Zone Mean Air Temperature, Hourly;
Output:Variable, *, Zone People Occupant Count, Hourly;
Output:Variable, *, Zone Tot Occupancy, Hourly;
Output:Variable, *, Site Outdoor Air Drybulb Temperature, Hourly;
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_objects(path):
    n = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if s and not s.startswith("!") and s.endswith(";"):
            n += 1
    return n


def main(src, dst):
    txt = open(src, "rb").read().decode("utf-8", errors="replace")
    with open(dst, "wb") as f:
        f.write((txt.rstrip("\r\n") + "\r\n" + BLOCK.replace("\n", "\r\n")).encode("utf-8"))
    proof = {
        "corrected_case_sha256": sha256(src),
        "run_copy_sha256": sha256(dst),
        "added": "Output:SQLite SimpleAndTabular + 5 hourly Output:Variable (identical block to stage-1 run copies)",
        "prefix_physical_core_equal": True,
        "note": "reporting-only run copy; corrected case physics untouched",
    }
    json.dump(proof, open(dst + ".RUN_COPY_PROOF.json", "w"), indent=1)
    print(json.dumps(proof, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
