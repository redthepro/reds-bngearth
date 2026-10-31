[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$Revision = 'aae4d6126d65a07315d0b28cc4a81dd68d86280a'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $packageRoot 'source'
$stateRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'beamng-earth-2'

if (Test-Path -LiteralPath $destination) {
    throw "Refusing to overwrite existing source directory: $destination. Inspect and use it explicitly."
}
$null = Get-Command git -ErrorAction Stop
if (-not $PSCmdlet.ShouldProcess($destination, 'Clone isolated source and create earth2/development at the reviewed revision')) {
    return
}

& git clone --no-checkout --filter=blob:none --single-branch -- 'https://github.com/redthepro/reds-bngearth.git' $destination
if ($LASTEXITCODE -ne 0) { throw 'Clone failed. Any partial directory is left intact for inspection.' }

& git -C $destination switch --create earth2/development $Revision
if ($LASTEXITCODE -ne 0) { throw 'Could not check out the reviewed revision. Source directory is left intact.' }

$actualRevision = (& git -C $destination rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRevision -ne $Revision) {
    throw 'Revision verification failed.'
}
foreach ($name in @('cache', 'runtime', 'benchmarks', 'assets')) {
    $null = New-Item -ItemType Directory -Path (Join-Path $stateRoot $name) -Force
}
[pscustomobject]@{
    Source = $destination
    Branch = 'earth2/development'
    Revision = $actualRevision
    StateRoot = $stateRoot
    DependenciesInstalled = $false
    GameStarted = $false
}
