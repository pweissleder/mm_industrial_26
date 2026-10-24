#requires -Version 5.1
# Internal helpers. No environment/profile/registry changes; DPAPI CurrentUser only.
Set-StrictMode -Version Latest

function Get-SyneraKeyPath {
    param([ValidateSet('Anthropic', 'ElevenLabs')][string]$Provider,
          [string]$TestScope = '')
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Key storage requires Windows.' }
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $directory = [IO.Path]::GetFullPath((Join-Path $local 'SyneraVoicePOC/secrets'))
    if ($TestScope) {
        if ($TestScope -notmatch '^[a-f0-9]{32}$') { throw 'Invalid test scope.' }
        $directory = Join-Path $directory ('test-' + $TestScope)
    }
    # Refuse Git/reparse ancestors rather than storing credentials in a redirected repo.
    $ancestor = $directory
    while ($ancestor) {
        if (Test-Path -LiteralPath (Join-Path $ancestor '.git')) {
            throw 'The secret store must be outside Git repositories.'
        }
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Redirected secret-store paths are not allowed.'
            }
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    Join-Path $directory ($Provider.ToLowerInvariant() + '.dpapi')
}

function Save-SyneraKey {
    param([ValidateSet('Anthropic', 'ElevenLabs')][string]$Provider,
          [Parameter(Mandatory)][Security.SecureString]$SecureKey,
          [string]$TestScope = '')
    if ($SecureKey.Length -eq 0) { throw 'An empty key cannot be registered.' }
    $path = Get-SyneraKeyPath -Provider $Provider -TestScope $TestScope
    if ((Test-Path -LiteralPath $path) -and
        ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Redirected key files are not allowed.'
    }
    $directory = Split-Path -Parent $path
    [void][IO.Directory]::CreateDirectory($directory)
    $temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.tmp')
    $encrypted = $null
    try {
        # No -Key/-SecureKey: Windows DPAPI encrypts for the current Windows user.
        $encrypted = ConvertFrom-SecureString -SecureString $SecureKey -ErrorAction Stop
        [IO.File]::WriteAllText($temporary, $encrypted, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temporary, $path, $true)
    } finally {
        $encrypted = $null
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
