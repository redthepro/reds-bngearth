[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$previousToken = [Environment]::GetEnvironmentVariable('SKETCHFAB_API_TOKEN', 'Process')
$loadedHere = [string]::IsNullOrWhiteSpace($previousToken)
$headers = $null
try {
    if ($loadedHere) {
        $null = & (Join-Path $PSScriptRoot 'Use-SketchfabCredential.ps1')
    }
    $headers = @{ Authorization = 'Token ' + $env:SKETCHFAB_API_TOKEN }
    try {
        $response = Invoke-WebRequest -Uri 'https://api.sketchfab.com/v3/me' -Method Get -Headers $headers -TimeoutSec 30 -MaximumRedirection 0 -UseBasicParsing
        [pscustomobject]@{
            Authenticated = ([int]$response.StatusCode -eq 200)
            HttpStatus = [int]$response.StatusCode
            Endpoint = 'https://api.sketchfab.com/v3/me'
            DownloadPermissionTested = $false
        }
    } catch {
        $statusCode = $null
        $responseProperty = $_.Exception.PSObject.Properties['Response']
        if ($null -ne $responseProperty -and $null -ne $responseProperty.Value) {
            $statusCode = [int]$responseProperty.Value.StatusCode
        }
        [pscustomobject]@{
            Authenticated = $false
            HttpStatus = $statusCode
            Endpoint = 'https://api.sketchfab.com/v3/me'
            DownloadPermissionTested = $false
            Note = 'Authentication check did not succeed. Token, identity and response body are intentionally omitted.'
        }
    }
} finally {
    if ($null -ne $headers) { $headers.Clear() }
    if ($loadedHere) {
        [Environment]::SetEnvironmentVariable('SKETCHFAB_API_TOKEN', $previousToken, 'Process')
    }
}
