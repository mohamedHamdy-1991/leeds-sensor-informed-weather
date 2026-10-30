# leeds-sensor-informed-weather

Reference implementation of the sensor-informed weather-file workflow from
"A reproducible workflow for sensor-informed urban residential overheating
assessment: a Leeds case study".

## Scope

This repository contains the **general, reusable** components of the workflow:

- `src/epw_morph.py` — the frozen morph algorithm: two-stage equal-site
  month-hour climatology with the documented fallback hierarchy, applied to a
  licensed parent EPW (temperature and vapour pressure; saturation bounding;
  wind/radiation untouched). Golden tests in `tests/`.
- `src/lapse_sensitivity.py` — departure diagnostics and the clearly labelled
  HYPOTHETICAL lapse-rate sensitivity (not a measured correction).
- `src/extract_tm59.py`, `src/make_run_copy.py`, `src/run_guarded.py` —
  EnergyPlus run conventions: guarded execution with resource checks, the
  stage-1 run-copy convention, and TM59 result extraction from the run SQL.
- `src/deliver_reviewed_results.py` — hash-verified, atomic delivery of
  curated results.

## Not included (and why)

- **Licensed CIBSE weather files** (the DSY parents): redistribution is not
  permitted. `epw_morph.morph_epw` accepts any user-supplied licensed parent.
- **Raw sensor data and restricted collaborator data**: available per the
  manuscript's data-availability statement; the public CC BY 4.0 microclimate
  release (doi.org/10.25448/lbu.31431745.v1) covers 16 Apr 2023 - 31 Aug 2024
  and is linked with attribution (it does not cover the study's extension to
  December 2025).
- **Author manuscripts and full-text PDFs**.

## Quick start

```bash
pip install numpy pandas          # stdlib otherwise
python -m pytest tests/           # or: python tests/test_epw_morph.py
python src/lapse_sensitivity.py --paired PAIRED.csv --sites SITES.csv \
       --ref-elevation 262.0 --outdir OUT
```

`epw_morph.build_climatology` expects a paired DataFrame with columns
`[band, lcz, month, hour, site, value]` (one row per paired observation);
`morph_epw(parent, out, clim_T, clim_E, "band:Centre")` writes the conditional
EPW and returns a written-bytes QC summary including the saturation-bound count.

## Provenance

`docs/provenance.md` records the version pins, the accepted algorithm
constants (Magnus/Tetens: e0 = 610.94 Pa, A = 17.625, B = 243.04; minimum
two-site support; L1-L5 fallback order), and the hash-verified campaign
artefacts this code reproduces. Changes to constants or fallback order change
results and must not be made silently.

## Licence

MIT (see LICENSE). Third-party code retains its own notices.
