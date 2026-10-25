#requires -Version 5.1
# Runs the live dialog matrix (backend/scripts/dialog_sim.py --live) with the DPAPI-protected
# Anthropic key (registered once with Register-ApiKey.ps1) injected into that one child process
# only, like Start-AriNav.ps1 -Live does for the backend. Nothing is written to .env or printed.
# Usage: powershell -File scripts/windows/Run-LiveDialogs.ps1 -MaxUsd 2 -Label night_live
#        [-Only id1,id2] [-DatasetsDir <folder>] [-Effort medium]
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateRange(0.01, 6)][double]$MaxUsd,
      [Parameter(Mandatory)][ValidatePattern('^[a-z0-9_]+$')][string]$Label,
      [string]$Only = '',
      [string]$DatasetsDir = '',
      [ValidateSet('low', 'medium', 'high')][string]$Effort = 'medium')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backendDir = Join-Path $root 'backend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'backend\.venv not found (uv sync first).' }

$run = New-Object Diagnostics.ProcessStartInfo
$run.FileName = $python
$run.WorkingDirectory = $backendDir
$run.UseShellExecute = $false
$arguments = @('scripts/dialog_sim.py', '--live', '--max-usd', $MaxUsd.ToString([Globalization.CultureInfo]::InvariantCulture),
               '--label', $Label, '--effort', $Effort)
if ($Only) { $arguments += @('--only', $Only) }
if ($DatasetsDir) { $arguments += @('--datasets-dir', (Resolve-Path $DatasetsDir).Path) }
$run.Arguments = ($arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
[void]$run.Environment.Remove('ANTHROPIC_API_KEY')

$process = $null
try {
    $path = Get-SyneraKeyPath -Provider 'Anthropic'
    if (-not (Test-Path -LiteralPath $path)) { throw 'The Anthropic key is missing. Run Register-ApiKey.ps1 -Provider Anthropic.' }
    $secure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
    $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $run.Environment['ANTHROPIC_API_KEY'] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer)
        $secure.Dispose()
    }
    $process = [Diagnostics.Process]::Start($run)
} finally {
    # Start() has copied the environment; drop the parent-side copy.
    [void]$run.Environment.Remove('ANTHROPIC_API_KEY')
}
$process.WaitForExit()
exit $process.ExitCode
