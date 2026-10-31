[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This helper uses Windows user-bound DPAPI.' }
$stateRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'beamng-earth-2'
$secretPath = Join-Path $stateRoot 'secrets\sketchfab.dpapi'
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
    throw 'No saved credential. Run Set-SketchfabCredential.ps1 and enter the token privately.'
}

$secureToken = $null
$pointer = [IntPtr]::Zero
try {
    $secureToken = ConvertTo-SecureString -String ([System.IO.File]::ReadAllText($secretPath).Trim())
    $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $env:SKETCHFAB_API_TOKEN = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    if ($null -ne $secureToken) { $secureToken.Dispose() }
}
Write-Output 'Sketchfab token loaded into this PowerShell process. Child processes inherit it; other sessions do not.'
