#requires -Version 7.0
# Starts the unified Navigation Lab. -Live injects the DPAPI-protected Anthropic and ElevenLabs keys
# (registered once via scripts\Register-ApiKey.ps1) into this server process only.
[CmdletBinding()]
param([switch]$Live,
      [ValidateRange(1024,65535)][int]$Port = 8770,
      [string]$VoiceId = 'JBFqnCBsd6RMkjVDRZzb')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$here = Split-Path -Parent $PSScriptRoot
$python = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'The prototype venv is missing (.venv).' }
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = $python
$start.WorkingDirectory = $here
$start.UseShellExecute = $false
foreach ($a in @('-B', 'server.py', '--port', "$Port")) { $start.ArgumentList.Add($a) }
if (-not $Live) { $start.ArgumentList.Add('--offline') }
foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY', 'XI_API_KEY')) { [void]$start.Environment.Remove($name) }
$start.Environment['SYNERA_VOICE_ID'] = $VoiceId
$child = $null
try {
    if ($Live) {
        foreach ($provider in @('Anthropic', 'ElevenLabs')) {
            $path = Get-SyneraKeyPath -Provider $provider
            if (-not (Test-Path -LiteralPath $path)) { throw "The $provider key is missing. Run Register-ApiKey.ps1 -Provider $provider." }
            $secure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
            $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try {
                $name = if ($provider -eq 'Anthropic') { 'ANTHROPIC_API_KEY' } else { 'ELEVENLABS_API_KEY' }
                $start.Environment[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
            } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer); $secure.Dispose() }
        }
    }
    Write-Host "Navigation Lab: http://127.0.0.1:$Port  (Ctrl+C to stop; mode: $(if ($Live) {'live'} else {'offline'}))"
    $child = [Diagnostics.Process]::Start($start)
} finally {
    foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY')) { [void]$start.Environment.Remove($name) }
}
try { $child.WaitForExit(); exit $child.ExitCode } finally { if ($child) { $child.Dispose() } }
