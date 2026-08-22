# Changelog

## 0.3.0 (2026-08-22)

FitPeek 0.3 extends the 0.2 light-curve workflow with a mission-aware batch
extractor and makes event/time-column handling more tolerant of real-world FITS
products.

### Changes from 0.2

- Added targeted Fermi/GBM and GECAM extraction with mission validation, recursive input discovery, mission mismatch summaries, energy presets, output estimates, cooperative cancellation, and manifest output.
- Added Fermi and GECAM event/light-curve exports, including GECAM multi-HDU and high/low-gain detector splitting.
- Added selectable time and numeric plot columns instead of requiring a column named exactly `TIME` and always plotting event counts.
- Added configurable global light-curve defaults and automatic trigger-relative time windows derived from actual file coverage.
- Added broader trigger-time discovery with source keyword reporting, plus direct `ENERGY` column range detection.
- Improved independent analysis/extractor window behavior and preserved per-file manual settings.
- Added automatic SHA-256 generation to the Windows build process.

### Debug and reliability fixes

- Fixed background fitting when background intervals lie outside the exported display range.
- Fixed valid zero-count background windows being treated as extraction failures.
- Fixed finite energy bands silently accepting every event when PHA channels cannot be mapped through EBOUNDS.
- Fixed an Extractor completion/Qt thread shutdown race that could crash when a window was closed immediately after processing.
- Fixed non-finite chart points and improved time-bound fallback for unusual FITS tables.
- Fixed stale portable-package checksum files after rebuilding.
- Normalized GECAM detector names without leading zeroes.
- Expanded CI and smoke tests to cover Extractor processing, cancellation, output estimation, energy mapping, and thread cleanup.

## 0.2.0 (2026-08-19)

FitPeek 0.2 is a substantial update over 0.1. It keeps the original read-only FITS inspection workflow while adding a complete quick-look light-curve analysis workflow.

### Light-curve analysis

- Added source replacement, individual FITS selection, and related-file selection for combining compatible event files.
- Added source, detector, time-system, event-table, and column-format validation before combining files.
- Added external background fitting with multiple intervals, full-file automatic edge intervals, weighted linear coefficients, covariance, fit-bin counts, and burst-tail warnings.
- Background fitting now reuses coefficients when only the displayed time range changes; data-selection changes trigger a refit.
- Added shaded background intervals and a background line to the chart, both clipped to the displayed time range.
- Added default display range `Tstart=-30` and `Tend=60` for new sources.
- Added persistent per-FITS analysis settings for time range, DT, energy, GTI, FLAG, EVT_TYPE, selected HDUs, Y mode, and background intervals.
- Added configurable chart font scaling and compact multi-detector/multi-source chart labels.

### Export and provenance

- Text output is the default for event sequences and light-curve data.
- Light-curve exports include raw counts, rates, background rates, net rates, and propagated errors; disabled background columns are filled with `nan`.
- Default filenames are shorter and include compact detector, time, energy, and output-type identifiers, with collision-resistant suffixes.
- Exported text and image files include software version, UTC generation time, source metadata, filters, background coefficients, fit intervals, fit-bin counts, and source hashes.

### Session and comparison fixes

- Fixed FITS header comparison so the file chosen from the session context menu opens on the left by default.
- Removing a FITS from the Session closes dependent light-curve windows, releases the reader, and removes its saved analysis settings without deleting the original FITS file.

### Build and verification

- Updated application and Windows file metadata to version 0.2.
- Reduced packaged dependencies by excluding unused scientific and visualization modules.
- Added smoke coverage for background fitting, settings persistence, clipped chart ranges, collision-safe names, and Session cleanup.

## 0.1.0

Initial public release with FITS browsing, table preview, header comparison, session management, light-curve preview, filtering, export, themes, and Windows packaging.
