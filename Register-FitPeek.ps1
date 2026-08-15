$ErrorActionPreference = 'Stop'

$exe = Join-Path $PSScriptRoot 'FitPeek.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw 'FitPeek.exe must be in the same folder as this script.'
}

$classes = 'HKCU:\Software\Classes'
$progId = 'FitPeek.FITS'
$command = '"{0}" "%1"' -f $exe
$extensions = '.fits', '.fit', '.fits.gz', '.evt', '.pha', '.rsp', '.rsp2', '.rm'

New-Item -Path "$classes\$progId\DefaultIcon" -Force | Out-Null
New-Item -Path "$classes\$progId\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$classes\$progId" -Name '(Default)' -Value 'FITS file'
Set-ItemProperty -Path "$classes\$progId\DefaultIcon" -Name '(Default)' -Value ('"{0}",0' -f $exe)
Set-ItemProperty -Path "$classes\$progId\shell\open\command" -Name '(Default)' -Value $command

foreach ($ext in $extensions) {
    $extensionKey = "$classes\$ext"
    New-Item -Path "$extensionKey\OpenWithProgids" -Force | Out-Null
    Set-ItemProperty -Path $extensionKey -Name '(Default)' -Value $progId
    New-ItemProperty -Path "$extensionKey\OpenWithProgids" -Name $progId -Value '' -PropertyType String -Force | Out-Null
}

$applicationKey = "$classes\Applications\FitPeek.exe"
New-Item -Path "$applicationKey\shell\open\command" -Force | Out-Null
New-Item -Path "$applicationKey\SupportedTypes" -Force | Out-Null
Set-ItemProperty -Path "$applicationKey\shell\open\command" -Name '(Default)' -Value $command
foreach ($ext in $extensions) {
    New-ItemProperty -Path "$applicationKey\SupportedTypes" -Name $ext -Value '' -PropertyType String -Force | Out-Null
}

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ShellRefresh {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
[ShellRefresh]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)

Write-Host 'FitPeek is registered for FITS files for the current Windows user.' -ForegroundColor Green
Write-Host 'If Windows keeps an existing default app, right-click a FITS file, choose Open with, select FitPeek, and enable Always.'
Read-Host 'Press Enter to close'
