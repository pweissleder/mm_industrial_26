#requires -Version 7.0
# Starts AriNav (FastAPI backend + Astro frontend) on Windows.
# -Live injects the DPAPI-protected Anthropic and ElevenLabs keys (registered once with
# Register-ApiKey.ps1) into the backend process only; nothing is written to .env.
# Usage: pwsh scripts/windows/Start-AriNav.ps1 [-Live] [-DatasetsDir <folder of building datasets>]
[CmdletBinding()]
param([switch]$Live,
      [string]$DatasetsDir = '',
      [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
      [string]$Uv = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$uv = if ($Uv) { $Uv } else { (Get-Command uv -ErrorAction SilentlyContinue)?.Source }
if (-not $uv) { throw 'uv is required (https://docs.astral.sh/uv/) or pass -Uv <path to uv.exe>.' }

$backend = [Diagnostics.ProcessStartInfo]::new()
$backend.FileName = $uv
$backend.WorkingDirectory = Join-Path $root 'backend'
$backend.UseShellExecute = $false
foreach ($a in @('run', 'uvicorn', 'app.main:app', '--port', "$BackendPort")) { $backend.ArgumentList.Add($a) }
foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY')) { [void]$backend.Environment.Remove($name) }
if ($DatasetsDir) { $backend.Environment['DATASETS_DIR'] = (Resolve-Path $DatasetsDir).Path }

$frontend = [Diagnostics.ProcessStartInfo]::new()
$frontend.FileName = (Get-Command npm.cmd).Source
$frontend.WorkingDirectory = Join-Path $root 'frontend'
$frontend.UseShellExecute = $false
$frontend.ArgumentList.Add('run'); $frontend.ArgumentList.Add('dev')
$frontend.Environment['BACKEND_URL'] = "http://localhost:$BackendPort"

$processes = @()
try {
    if ($Live) {
        foreach ($provider in @('Anthropic', 'ElevenLabs')) {
            $path = Get-SyneraKeyPath -Provider $provider
            if (-not (Test-Path -LiteralPath $path)) { throw "The $provider key is missing. Run Register-ApiKey.ps1 -Provider $provider." }
            $secure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
            $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try {
                $name = if ($provider -eq 'Anthropic') { 'ANTHROPIC_API_KEY' } else { 'ELEVENLABS_API_KEY' }
                $backend.Environment[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
            } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer); $secure.Dispose() }
        }
    }
    $processes += [Diagnostics.Process]::Start($backend)
} finally {
    foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY')) { [void]$backend.Environment.Remove($name) }
}
$processes += [Diagnostics.Process]::Start($frontend)
Write-Host "AriNav: http://localhost:4321  (backend http://localhost:$BackendPort, mode: $(if ($Live) {'live'} else {'offline'})). Ctrl+C stops both."
try { $processes | ForEach-Object { $_.WaitForExit() } }
finally { $processes | Where-Object { -not $_.HasExited } | ForEach-Object { $_.Kill($true) } }
