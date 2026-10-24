#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('Anthropic', 'ElevenLabs')][string]$Provider)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$key = $null
try {
    $key = Read-Host -Prompt "$Provider API key (hidden input; replaces an existing registration)" -AsSecureString
    Save-SyneraKey -Provider $Provider -SecureKey $key
    Write-Host "$Provider key encrypted with Windows DPAPI for this user."
} catch {
    Write-Error 'Registration failed. Check Windows, the storage path, and the input.' -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $key) { $key.Dispose() }
    $key = $null
}
