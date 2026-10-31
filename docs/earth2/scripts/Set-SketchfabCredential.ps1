[CmdletBinding()]
param(
    [System.Security.SecureString]$Token
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This helper uses Windows user-bound DPAPI.' }
if ($null -eq $Token) { $Token = Read-Host 'Sketchfab API token (hidden)' -AsSecureString }
if ($Token.Length -eq 0) { throw 'The token cannot be empty.' }

$stateRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'beamng-earth-2'
$secretDirectory = Join-Path $stateRoot 'secrets'
$secretPath = Join-Path $secretDirectory 'sketchfab.dpapi'
$null = New-Item -ItemType Directory -Path $secretDirectory -Force
$encrypted = ConvertFrom-SecureString -SecureString $Token
$temporaryPath = Join-Path $secretDirectory ([Guid]::NewGuid().ToString('N') + '.tmp')
try {
    [System.IO.File]::WriteAllText($temporaryPath, $encrypted, [System.Text.Encoding]::ASCII)
    Move-Item -LiteralPath $temporaryPath -Destination $secretPath -Force
} finally {
    if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force }
    $encrypted = $null
}
Write-Output 'Sketchfab credential saved with Windows DPAPI for this user. No plaintext token was written.'
