# Provenance and version pins

## Frozen algorithm constants (do not change silently)
- Magnus/Tetens over water: e0 = 610.94 Pa, A = 17.625, B = 243.04 (protocol s9; the only constant set used)
- Minimum site support per cell: 2 (contract s4)
- Fallback order: L1 group x month x hour -> L2 group x season x hour -> L3 all-site month x hour -> L4 all-site season x hour -> L5 all-site global
- Two-stage equal-site weighting: per-site mean first, unweighted mean over contributing sites
- EPW columns modified: dry-bulb, dew point, RH only; wind speed/direction, radiation and sky infrared byte-preserved

## Hash-verified campaign artefacts reproduced by this code
- High50 parent EPW sha256 ab17979f299a6517cc7069e2512bae2c154a8f83468f199e159c8dcbcf9e0271
- High10 parent EPW sha256 867cf0302f1d74869d2aea69865e0fd9419d177609221c30ab03e3d204fb492e
- EnergyPlus 23.1.0-87ed9199d4 binary sha256 9832298eb181205d... (run_guarded.py refuses other binaries)
- Paired cohort v2: 816,610 temperature rows; 732,945 moisture rows; 4 quarantined rows excluded

## Environment
- Python >= 3.11; numpy, pandas for epw_morph/lapse_sensitivity; stdlib only for the run tools
- The study executed on macOS arm64; results are deterministic per (IDF, EPW, engine) triple
