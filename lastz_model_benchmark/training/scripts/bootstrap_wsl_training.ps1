<#
Starts the project-local WSL setup script.  The default is a harmless dry run;
pass -Execute to allow dependency installation into training/.venv-wsl-cu126.
#>
[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "bootstrap_wsl_training.sh"
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Missing WSL bootstrap script: $scriptPath"
}

$wslScript = (& wsl.exe --distribution $Distro -- wslpath -a $scriptPath).Trim()
if (-not $wslScript) {
    throw "Could not translate the bootstrap path into WSL."
}

$arguments = @("--distribution", $Distro, "--", "bash", $wslScript)
if ($Execute) {
    $arguments += "--execute"
}
& wsl.exe @arguments
exit $LASTEXITCODE
