# Changelog

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
