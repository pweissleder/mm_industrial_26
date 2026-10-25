#requires -Version 5.1
# Starts AriNav (FastAPI backend + Astro frontend) on Windows; Windows PowerShell 5.1 or 7.
# -Live injects the DPAPI-protected Anthropic and ElevenLabs keys (registered once with
# Register-ApiKey.ps1) into the backend process only; nothing is written to .env or the console.
# Usage: powershell -File scripts/windows/Start-AriNav.ps1 [-Live] [-DatasetsDir <folder>]
#        [-BackendPort 8000] [-Uv <path to uv.exe>] [-Profile core|immersive|showcase]
# -Profile sets the default feature profile (URL ?profile= still overrides it).
# -Phone serves the frontend over HTTPS on the local network (self-signed dev certificate, nothing
#  installed) so a phone can use rotation; open https://<this PC's IP>:4321 and accept the warning.
#  Windows may ask once whether Node may accept connections on private networks.
# The backend runs with `uv run` when uv is found, otherwise with backend/.venv (uv sync first).
[CmdletBinding()]
param([switch]$Live,
      [string]$DatasetsDir = '',
      [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
      [string]$Uv = '',
      [ValidateSet('', 'core', 'immersive', 'showcase')][string]$Profile = '',
      [switch]$Phone)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backendDir = Join-Path $root 'backend'
$keyNames = @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY')

$backend = New-Object Diagnostics.ProcessStartInfo
$backend.WorkingDirectory = $backendDir
$backend.UseShellExecute = $false
$uvicorn = "uvicorn app.main:app --port $BackendPort"
if (-not $Uv) {
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { $Uv = $found.Source }
}
if ($Uv) {
    $backend.FileName = $Uv
    $backend.Arguments = "run $uvicorn"
} else {
    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Neither uv nor backend\.venv found. Install uv (https://docs.astral.sh/uv/) and run "uv sync" in backend.'
    }
    $backend.FileName = $python
    $backend.Arguments = "-m $uvicorn"
}
foreach ($name in $keyNames) { [void]$backend.Environment.Remove($name) }
if ($DatasetsDir) { $backend.Environment['DATASETS_DIR'] = (Resolve-Path $DatasetsDir).Path }

$frontend = New-Object Diagnostics.ProcessStartInfo
$frontend.FileName = (Get-Command npm.cmd).Source
$frontend.Arguments = 'run dev'
$frontend.WorkingDirectory = Join-Path $root 'frontend'
$frontend.UseShellExecute = $false
$frontend.Environment['BACKEND_URL'] = "http://localhost:$BackendPort"
if ($Profile) { $frontend.Environment['PUBLIC_ARINAV_PROFILE'] = $Profile }
if ($Phone) { $frontend.Environment['ARINAV_PHONE'] = '1' }

$processes = @()
try {
    if ($Live) {
        foreach ($provider in @('Anthropic', 'ElevenLabs')) {
            $path = Get-SyneraKeyPath -Provider $provider
            if (-not (Test-Path -LiteralPath $path)) {
                throw "The $provider key is missing. Run Register-ApiKey.ps1 -Provider $provider."
            }
            $secure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
            $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try {
                $name = if ($provider -eq 'Anthropic') { 'ANTHROPIC_API_KEY' } else { 'ELEVENLABS_API_KEY' }
                $backend.Environment[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer)
                $secure.Dispose()
            }
        }
    }
    $processes += [Diagnostics.Process]::Start($backend)
} finally {
    # Start() has copied the environment; drop the parent-side copies.
    foreach ($name in $keyNames) { [void]$backend.Environment.Remove($name) }
}
$processes += [Diagnostics.Process]::Start($frontend)
$mode = if ($Live) { 'live' } else { 'offline' }
if ($Phone) {
    # Connected adapters only (Wi-Fi, LAN, this PC's mobile hotspot), no Hyper-V/WSL switches.
    $up = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceAlias -notmatch '^vEthernet' }).InterfaceIndex
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and ($up -contains $_.InterfaceIndex) }
    foreach ($a in $addresses) {
        # Windows' mobile hotspot always hands out 192.168.137.x and is reached at .1
        $where = if ($a.IPAddress -like '192.168.137.*') { 'phone on this PC''s hotspot' } else { "phone in the same network as '$($a.InterfaceAlias)'" }
        Write-Host "Phone: https://$($a.IPAddress):4321  ($where; accept the certificate warning once)"
    }
}
$scheme = if ($Phone) { 'https' } else { 'http' }
Write-Host "AriNav: ${scheme}://localhost:4321  (backend http://localhost:$BackendPort, mode: $mode). Ctrl+C stops both."
try { foreach ($p in $processes) { $p.WaitForExit() } }
finally {
    foreach ($p in $processes) {
        if (-not $p.HasExited) { & taskkill.exe /PID $p.Id /T /F | Out-Null }
    }
}
