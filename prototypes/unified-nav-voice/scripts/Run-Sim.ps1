#requires -Version 7.0
# Live dialog simulation: injects only the DPAPI-protected Anthropic key into the Python child.
# Usage: pwsh scripts/Run-Sim.ps1 --live --max-usd 1.0 --label live1   (arguments go to sim/run_sim.py)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'KeyStore.ps1')
$code = Invoke-SyneraPython -Provider Anthropic -PythonArguments (@('-B', 'sim/run_sim.py') + $args)
exit $code
