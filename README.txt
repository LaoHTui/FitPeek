FitPeek MVP
===========

FitPeek is a read-only FITS structure and table previewer for Windows.

Supported input: .fits, .fit, .fits.gz and common FITS-style extensions.

Features include a persistent multi-file session tree, file summaries,
first/last/custom row previews, recent files, drag-and-drop opening, FITS
Header comparison, and dedicated GTI and EBOUNDS views.
Use Delete while the session tree is focused to remove a file from the
session without deleting it from disk. Choose View -> Theme to use the
system, light, or dark Qt color scheme.

Right-click a file or HDU in the session tree to open a light curve window,
start a Header comparison, copy the file path, expand or collapse the file,
or remove it from the session.

Table previews can be filtered across all visible columns and sorted by
clicking a column header. Select one or more rows and choose Export selected
rows to write a CSV containing the original FITS row numbers. Filtering,
sorting, and row export apply to the current preview window (up to 5,000
rows), not to rows outside that window.

Choose View -> Compare FITS Headers to compare any two HDUs currently in the
session. Repeated header cards are matched by occurrence, and the comparison
can be limited to changed or matching cards.

Application icon
----------------
The source icon is assets\fitpeek.ico. It contains Windows icon sizes from
16x16 through 256x256 and is used for the window, taskbar, and packaged exe.
To replace it, overwrite that file with another multi-resolution .ico and
run build.ps1 again. assets\fitpeek.png is only a preview.

Light curve tool
----------------
Select a FITS file and open View -> New Light Curve Window. The tool can merge selected
event HDUs, filter by time, EBOUNDS energy range, GTI, FLAG and EVT_TYPE,
then bin events with a configurable DT. Calculation is read-only and runs in
the background. Results remain in memory until Events, Light Curve Data, or
Image is explicitly saved with the corresponding button.

View -> New Light Curve Window opens a separate compact window every time.
Multiple windows may use the same FITS file or different files, so their
settings and generated curves can be compared side by side.
The Event HDUs and Binning and filters sections can be collapsed with their
arrow buttons to give the generated chart most of the window.

FLAG and EVT_TYPE filters can be independently enabled and set to any integer
value. The preview uses a white background, a black step histogram, Poisson error bars,
and a small X-axis margin around the requested time range.

Only complete, equal-width DT bins are included. If the requested time span
contains a remainder shorter than one DT, that partial tail is reported and
omitted so it cannot create an artificially low final point. The event HDU
selector uses a wrapping grid with All, None, and Invert controls.

When relative trigger time is active, the chart marks T=0 with a red dashed
T0 line. Dense previews automatically preserve extrema while reducing the
number of drawn line points and error bars according to the chart width; full
exported arrays are never downsampled.

Usage
-----
1. Run FitPeek.exe and choose File -> Open, drop FITS files onto the window,
   use File -> Open Recent, or pass a FITS path on the command line.
2. Double-click Bind-FitPeek.cmd to register FitPeek for FITS files for the current user.
3. If Windows keeps an existing default app, right-click a FITS file, choose Open with, select FitPeek, and enable Always.

The original FITS file is never modified. Large tables are previewed by reading only the selected window.

Build
-----
Run build.ps1 on a Windows machine with Python 3.12+ installed. It creates
dist\FitPeek\FitPeek.exe and FitPeek_Portable.zip. Keep the _internal folder
beside FitPeek.exe; those runtime files avoid unpacking the application again
on every launch.
