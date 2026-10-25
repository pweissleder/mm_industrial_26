#requires -Version 5.1
# Diagnoses the registered ElevenLabs key without showing it: injects it into one Python child
# process (like Start-AriNav.ps1 -Live) that prints only HTTP status codes and ElevenLabs' error
# details (quota, permissions, voice). Usage: powershell -File scripts/windows/Check-ElevenLabs.ps1
[CmdletBinding()]
param([string]$VoiceId = '',
      [string]$Text = 'Go through the door into Office corridor 0.16.')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backendDir = Join-Path $root 'backend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'
$code = @'
import json, os, sys
import httpx
key = os.environ.pop("ELEVENLABS_API_KEY")
voice = os.environ.get("CHECK_VOICE_ID") or ""
if not voice:
    sys.path.insert(0, ".")
    from app.config import get_settings
    voice = getattr(get_settings(), "elevenlabs_voice_id", "") or ""
h = {"xi-api-key": key}
def show(name, r):
    try:
        body = r.json()
    except Exception:
        body = r.text[:300]
    if isinstance(body, dict) and r.status_code == 200:
        keep = {k: body.get(k) for k in ("tier", "character_count", "character_limit",
                "next_character_count_reset_unix", "status") if k in body}
        body = keep or "ok"
    print(f"{name}: HTTP {r.status_code} {json.dumps(body)[:600]}")
with httpx.Client(base_url="https://api.elevenlabs.io", headers=h, timeout=30) as c:
    show("subscription", c.get("/v1/user/subscription"))
    show("voice", c.get(f"/v1/voices/{voice}"))
    for text in ("Test.", os.environ.get("CHECK_TEXT") or "Test."):
        r = c.post(f"/v1/text-to-speech/{voice}", params={"output_format": "pcm_16000"},
                   json={"text": text, "model_id": "eleven_flash_v2_5", "language_code": "en"})
        name = f"tts {len(text)} chars"
        if r.status_code == 200:
            print(f"{name}: HTTP 200 audio ok")
        else:
            show(name, r)
'@
$run = New-Object Diagnostics.ProcessStartInfo
$run.FileName = $python
$run.WorkingDirectory = $backendDir
$run.UseShellExecute = $false
$tmp = Join-Path $env:TEMP 'check_elevenlabs.py'
[IO.File]::WriteAllText($tmp, $code)
$run.Arguments = "`"$tmp`""
if ($VoiceId) { $run.Environment['CHECK_VOICE_ID'] = $VoiceId }
$run.Environment['CHECK_TEXT'] = $Text
$process = $null
try {
    $path = Get-SyneraKeyPath -Provider 'ElevenLabs'
    $secure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
    $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $run.Environment['ELEVENLABS_API_KEY'] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer)
        $secure.Dispose()
    }
    $process = [Diagnostics.Process]::Start($run)
} finally {
    [void]$run.Environment.Remove('ELEVENLABS_API_KEY')
}
$process.WaitForExit()
Remove-Item $tmp -ErrorAction SilentlyContinue
