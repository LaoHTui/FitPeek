# FitPeek 0.3.0

FitPeek 0.3 builds on the 0.2 light-curve analysis release with a dedicated
mission-aware batch Extractor, more flexible FITS column handling, and a set of
scientific-correctness and runtime stability fixes.

## What changed from 0.2

- New **Extractor** window (`Ctrl+E`) for Fermi/GBM and GECAM event products.
- Mission validation prevents unrelated FITS products from being interpreted as event data; skipped files are reported clearly.
- Batch light-curve and event-list generation supports energy bands, multiple bin widths, GTI filtering, relative time, background intervals, combined products, cancellation, estimates, and a JSON manifest.
- GECAM `EVENTSnn` tables and high/low gain streams are split into correctly named detector outputs.
- Light-curve analysis can select an alternative time column and aggregate a numeric data column in addition to ordinary event counts.
- Trigger-time discovery recognizes additional mission keywords and reports where the selected value came from.
- New global defaults control time-window percentages, DT, relative time, GTI, energy filtering, background fitting, and Y-axis mode.
- Direct `ENERGY` columns now initialize their usable range automatically.
- The Windows build now refreshes `FitPeek_Portable.zip.sha256` automatically.

## Debug fixes

- Fixed background fitting when fit intervals are outside the displayed/exported time range.
- Fixed zero-count energy selections and background windows so valid empty data does not abort a batch.
- Fixed unmapped PHA channels incorrectly passing finite energy filters.
- Fixed a Qt worker-thread shutdown race that could crash the Extractor when closed immediately after completion.
- Fixed non-finite values reaching chart series for unusual event data.
- Improved FITS time-bound fallback and trigger-time handling for products without a conventional `TIME`/`TRIGTIME` layout.
- Fixed stale SHA-256 files after rebuilding the portable package.

## Verification

- Core FITS/light-curve smoke tests passed.
- Extractor mission, energy, background, output-estimation, compressed-input, and cancellation tests passed.
- Offscreen UI workflow and thread-cleanup tests passed.
- Windows portable and standalone executables were rebuilt and launch-tested with a sample FITS file.

## Download

- `FitPeek_Portable.zip`: extract the complete archive, then run `FitPeek.exe`.
- `FitPeek_Portable.zip.sha256`: SHA-256 checksum for the archive.

FitPeek remains read-only with respect to source FITS files.
