param(
    [string]$PythonPath = $env:FITPEEK_PYTHON,
    [string]$IndexUrl = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    $bundled = if ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    }

    if ($PythonPath) {
        $explicitPython = Get-Command $PythonPath -ErrorAction SilentlyContinue
        if (-not $explicitPython) { throw "Python was not found at $PythonPath" }
        & $explicitPython.Source -m venv $venv
    } elseif ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\python.exe') {
        & $pythonCommand.Source -m venv $venv
    } elseif ($pyLauncher) {
        & $pyLauncher.Source -3 -m venv $venv
    } elseif ($bundled -and (Test-Path -LiteralPath $bundled)) {
        & $bundled -m venv $venv
    } else {
        Write-Host 'Python 3 is required. Install it from https://www.python.org/downloads/windows/' -ForegroundColor Yellow
        exit 1
    }
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
}
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
$pipArguments = @('-m', 'pip', 'install', '-r', (Join-Path $root 'requirements.txt'), '--timeout', '60')
if ($IndexUrl) { $pipArguments += @('--index-url', $IndexUrl) }
& $python @pipArguments
if ($LASTEXITCODE -ne 0) { throw 'Failed to install project dependencies.' }
Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name FitPeek `
        --exclude-module pandas --exclude-module matplotlib --exclude-module scipy `
        --exclude-module openpyxl --exclude-module lxml --exclude-module PIL `
        --exclude-module astropy.visualization --exclude-module astropy.coordinates `
        --exclude-module astropy.wcs --exclude-module astropy.cosmology `
        --exclude-module astropy.modeling --exclude-module astropy.convolution `
        --exclude-module astropy.timeseries --exclude-module astropy.stats `
        --exclude-module astropy.nddata --exclude-module astropy.samp `
        --exclude-module astropy.vo `
        --icon (Join-Path $root 'assets\fitpeek.ico') `
        --version-file (Join-Path $root 'assets\windows-version-info.txt') `
        --add-data "$(Join-Path $root 'assets\fitpeek.ico');assets" `
        --add-data "$(Join-Path $root 'assets\fitpeek.png');assets" app.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $distDir = Join-Path $root 'dist\FitPeek'
    Copy-Item (Join-Path $root 'Register-FitPeek.ps1'), (Join-Path $root 'Bind-FitPeek.cmd'), (Join-Path $root 'README.txt') -Destination $distDir -Force
    if (Test-Path 'FitPeek_Portable.zip') { Remove-Item 'FitPeek_Portable.zip' -Force }
    Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath (Join-Path $root 'FitPeek_Portable.zip')
} finally { Pop-Location }
Write-Host "Built $root\dist\FitPeek\FitPeek.exe"
