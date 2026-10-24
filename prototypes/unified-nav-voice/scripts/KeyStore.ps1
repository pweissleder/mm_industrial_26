#requires -Version 7.0
# Internal helpers. No environment/profile/registry changes; DPAPI CurrentUser only.
Set-StrictMode -Version Latest

function Get-SyneraKeyPath {
    param([ValidateSet('Anthropic', 'ElevenLabs')][string]$Provider,
          [string]$TestScope = '')
    if (-not $IsWindows) { throw 'Key storage requires Windows.' }
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

function Invoke-SyneraPython {
    param([ValidateSet('Anthropic', 'ElevenLabs', 'Both', '')][string]$Provider = '',
          [Parameter(Mandatory)][string[]]$PythonArguments,
          [string]$TestScope = '')
    $project = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $project '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'The project venv is missing. See README.md.' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.WorkingDirectory = $project
    $start.UseShellExecute = $false
    foreach ($argument in $PythonArguments) { $start.ArgumentList.Add($argument) }
    # The parent process is untouched; the child receives only its selected API key.
    foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY', 'XI_API_KEY')) {
        [void]$start.Environment.Remove($name)
    }
    $secure = $null
    $plain = $null
    $encrypted = $null
    $buffer = [IntPtr]::Zero
    $child = $null
    try {
        $providers = if ($Provider -eq 'Both') { @('Anthropic','ElevenLabs') } elseif ($Provider) { @($Provider) } else { @() }
        foreach ($selectedProvider in $providers) {
            $path = Get-SyneraKeyPath -Provider $selectedProvider -TestScope $TestScope
            if (-not (Test-Path -LiteralPath $path)) {
                throw 'The key is missing. Run Register-ApiKey.ps1 for this provider first.'
            }
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Redirected key files are not allowed.'
            }
            try {
                $encrypted = [IO.File]::ReadAllText($path)
                $secure = ConvertTo-SecureString -String $encrypted -ErrorAction Stop
                $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
                $name = if ($selectedProvider -eq 'Anthropic') { 'ANTHROPIC_API_KEY' } else { 'ELEVENLABS_API_KEY' }
                $start.Environment[$name] = $plain
            } catch { throw 'DPAPI decryption failed. Register the key again using the correct Windows account.' }
            finally {
                $plain = $null; $encrypted = $null
                if ($buffer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer) }
                if ($null -ne $secure) { $secure.Dispose() }
                $buffer = [IntPtr]::Zero; $secure = $null
            }
        }
        try { $child = [Diagnostics.Process]::Start($start) }
        catch { throw 'The Python child process could not be started.' }
    } finally {
        # Start() has copied the environment; drop all parent-side temporary copies now.
        foreach ($name in @('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY', 'XI_API_KEY')) {
            [void]$start.Environment.Remove($name)
        }
        $plain = $null
        $encrypted = $null
        if ($buffer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer) }
        if ($null -ne $secure) { $secure.Dispose() }
        $secure = $null
        $buffer = [IntPtr]::Zero
    }
    try {
        $child.WaitForExit()
        return $child.ExitCode
    } finally {
        if ($null -ne $child) { $child.Dispose() }
        $start = $null
    }
}
