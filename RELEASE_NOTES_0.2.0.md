# FitPeek 0.2.0

FitPeek 0.2 keeps the read-only FITS inspection features from 0.1 and adds a substantially more complete light-curve workflow.

## Highlights since 0.1

- Combine compatible FITS event files from the light-curve window, with source/time/format validation and detector-aware labels.
- Fit a weighted linear background over multiple user-defined intervals or the first and last 20% of the complete FITS time range.
- Display background lines and translucent interval shading; plotting is clipped to the selected `Tstart`/`Tend` window.
- Reuse background coefficients when only the display range changes, while refitting when the data selection changes.
- Save and restore analysis settings per FITS, including time range, binning, filters, energy selection, selected HDUs, Y mode, and background intervals.
- Use `Tstart=-30` and `Tend=60` as defaults for new sources.
- Export compact, collision-safe text filenames with detector/time/energy identifiers and detailed provenance headers.
- Add raw/background/net light-curve columns and `nan` placeholders when background fitting is disabled.
- Fix session header comparison source selection and clean saved settings when a source is removed from the Session.
- Add chart font scaling, compact multi-detector labels, fit coefficients, fit-bin counts, and automatic burst-tail warnings.

## Downloads

- `FitPeek_Portable.zip`: Windows portable package. Extract the complete archive and run `FitPeek.exe`.

The package is built by the repository's Windows release workflow. SHA-256 checksums are published alongside the archive.
