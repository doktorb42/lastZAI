[CmdletBinding()]
param(
    [string]$PackApk,
    [string]$BaseApk,
    [string]$ConfigApk,
    [string]$OutputRoot,
    [string]$Device = 'emulator-5554',
    [string]$ManifestVersion = 'auto',
    [switch]$InitCanary,
    [switch]$Install,
    [switch]$Reinstall,
    [switch]$AllowDowngrade,
    [switch]$RefreshFromDevice,
    [switch]$PreflightOnly,
    [switch]$AutoRepairCapture
)

<##
Build a new Last Z chat-capture split from an updated three-split installation.

The Lua bundle, manifest entry, and gameres.version are discovered from the
given install-time pack.  No prior bundle hash, Unity path id, CRC, or size is
an input.  The script never overwrites an APK input.

-Install attempts a normal replacement installation.
-Reinstall is explicit because the first conversion from ReadyGo's signature to
the local debug signature requires uninstalling the original package.  It backs
up external app files before the uninstall.  Internal Android app data cannot
be backed up without elevated device access.

-RefreshFromDevice exports the three currently installed splits into a new,
versioned apk\device_snapshots directory and patches that snapshot.  It never
overwrites apk\current or a previously captured APK.  Use it after an official
game update so the patched split set has the same versionCode as the device.

-AllowDowngrade is deliberately opt-in.  It passes Android's downgrade flag
only when a lower-version local split set is intentionally being installed.
It does not bypass Android signature checks.  -PreflightOnly performs the
version check and exits without building, signing, or installing anything.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($PackApk)) {
    $PackApk = Join-Path $scriptRoot 'apk\current\split_install_time_pack.apk'
}
if ([string]::IsNullOrWhiteSpace($BaseApk)) {
    $BaseApk = Join-Path $scriptRoot 'apk\current\base.apk'
}
if ([string]::IsNullOrWhiteSpace($ConfigApk)) {
    $ConfigApk = Join-Path $scriptRoot 'apk\current\split_config.arm64_v8a.apk'
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $scriptRoot 'patch\builds'
}

$updateScript = Join-Path $scriptRoot 'tools\patching\update_lastz_chat_capture.py'
$activeCacheScript = Join-Path $scriptRoot 'tools\patching\patch_active_lastz_chat_cache.py'
$replaceScript = Join-Path $scriptRoot 'tools\patching\replace_apk_asset_entry.py'

if (($Reinstall -or $AllowDowngrade) -and -not $Install) {
    throw '-Reinstall and -AllowDowngrade each require -Install.'
}
if ($PreflightOnly -and $Install) {
    throw '-PreflightOnly cannot be combined with -Install.'
}

$adb = 'C:\Program Files\BlueStacks_nxt\HD-Adb.exe'
$apksigner = 'C:\Users\doktorb\AppData\Local\Android\Sdk\build-tools\36.1.0\apksigner.bat'
$aapt = Join-Path (Split-Path -Parent $apksigner) 'aapt.exe'
$keystore = 'C:\Users\doktorb\.android\debug.keystore'
$package = 'com.readygo.barrel.gp'
# One-click PEva mode: update only active downloaded resources, never uninstall,
# never re-sign or replace the game's APK. The legacy APK flow below is unchanged.
if ($AutoRepairCapture) {
    # PowerShell 5.1 can promote native stderr to a terminating exception
    # under Stop. We inspect explicit exit codes for every native command.
    $ErrorActionPreference = 'Continue'
    if ($Reinstall -or $AllowDowngrade -or $Install -or $PreflightOnly -or $RefreshFromDevice) {
        throw 'AutoRepairCapture cannot be combined with APK installation flags.'
    }
    if (-not (Test-Path -LiteralPath $adb)) { throw "Nie znaleziono ADB: $adb" }
    # History and the fail-closed fallback live together in the legacy archive.
    # Keep this one-click script at the project root, but do not keep a second
    # maintenance Python entry point there just for backward compatibility.
    $selfContained = Join-Path $scriptRoot 'archive\legacy_runtime\update_patch.py'
    $workspace = Join-Path $scriptRoot 'data\device-backups\capture'
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $pythonLauncher) { throw 'Brak Python Launcher (py). Python 3 jest wymagany.' }
    $commonArgs = @('--adb', $adb, '--device', $Device, '--workspace', $workspace)

    function Invoke-OldCaptureTool {
        param([string]$Action, [switch]$Launch)
        $toolArgs = @('-3', '-B', $selfContained, $Action) + $commonArgs
        if ($Launch) { $toolArgs += '--start' }
        $lines = @(& py @toolArgs 2>&1)
        $exitCode = $LASTEXITCODE
        $allText = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
        return [pscustomobject]@{ ExitCode = $exitCode; Text = $allText }
    }

    function Invoke-DownloadedCacheTool {
        $out = Join-Path $workspace ("active_cache_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
        $lines = @(& py -3 -B $activeCacheScript --adb $adb --device $Device --package $package --out-dir $out 2>&1)
        $exitCode = $LASTEXITCODE
        $allText = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
        if ($exitCode -eq 0 -and $allText -match 'ACTIVE_CACHE_PATCH_OK|ACTIVE_CACHE_ALREADY_PATCHED') {
            # The cache script may force-stop Last Z. Start the installed app
            # via the platform's normal launcher without touching its data.
            $launch = @(& $adb -s $Device shell monkey -p $package -c android.intent.category.LAUNCHER 1 2>&1)
            if ($LASTEXITCODE -ne 0 -or ($launch -join "`n") -match 'No activities found|monkey aborted') {
                throw "Patch zapisany, ale nie udalo sie uruchomic Last Z: $($launch -join ' ')"
            }
            Write-Output 'PATCH_METHOD=ACTIVE_CACHE'
            if ($allText -match 'ACTIVE_CACHE_ALREADY_PATCHED') {
                Write-Output 'PATCH_PRESENT'
            } else {
                Write-Output 'PATCH_APPLIED'
            }
            exit 0
        }
        if ($exitCode -eq 4 -and $allText -match 'ACTIVE_CACHE_NOT_FOUND') {
            Write-Host '[AUTO] Brak aktywnego pobranego bundle. Sprawdzam alternatywna metode.'
            return $false
        }
        throw "Nieudana naprawa pobranego bundle: $allText"
    }

    # Try the active downloaded cache repair (newer maintained tool) first.
    # If it is absent, the packaged update_patch.py is a fail-closed fallback.
    if (Test-Path -LiteralPath $activeCacheScript) {
        $done = Invoke-DownloadedCacheTool
        if ($done) { exit 0 }
    }
    if (-not (Test-Path -LiteralPath $selfContained)) {
        throw 'Brak archive\legacy_runtime\update_patch.py oraz aktywnego patchera tools\patching\patch_active_lastz_chat_cache.py.'
    }
    # The fallback uses UnityPy. Install only its own Python dependency if absent.
    & py -3 -c 'import UnityPy' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[AUTO] Doinstalowuje UnityPy potrzebny do analizy plikow czatu...'
        & py -3 -m pip install --disable-pip-version-check 'UnityPy>=1.10,<2'
        if ($LASTEXITCODE -ne 0) { throw 'Nie udalo sie doinstalowac UnityPy. Niczego nie odinstalowano.' }
    }
    $check = Invoke-OldCaptureTool -Action status
    if ($check.ExitCode -ne 0) {
        throw "Nie mozna bezpiecznie zweryfikowac aktywnego ChatNetManager: $($check.Text)"
    }
    if ($check.Text -match '\[STATUS\] ACTIVE PROJECT CHAT BUNDLE LOOKS PATCHED\.') {
        Write-Output 'PATCH_METHOD=DOWNLOADED_BUNDLE'
        Write-Output 'PATCH_PRESENT'
        exit 0
    }
    if ($check.Text -notmatch '\[STATUS\] ACTIVE PROJECT CHAT BUNDLE IS NOT PATCHED YET\.') {
        throw "Nieznany wynik kontroli patcha: $($check.Text)"
    }
    Write-Host '[AUTO] Sygnatura patcha nieobecna. Naprawiam aktywny bundle (backup + SHA256).'
    $repair = Invoke-OldCaptureTool -Action apply -Launch
    if ($repair.ExitCode -ne 0 -or $repair.Text -notmatch 'Patch deployed and verified before game start') {
        throw "Nie mozna potwierdzic naprawy ChatNetManager: $($repair.Text)"
    }
    Write-Output 'PATCH_METHOD=DOWNLOADED_BUNDLE'
    Write-Output 'PATCH_APPLIED'
    exit 0
}

$toolFiles = @($adb, $apksigner, $aapt, $keystore, $updateScript, $activeCacheScript, $replaceScript)
foreach ($tool in $toolFiles) {
    if (-not (Test-Path -LiteralPath $tool)) { throw "Missing required file: $tool" }
}

function Get-ApkVersionCode {
    param([string]$ApkPath)

    $badging = @(& $aapt dump badging $ApkPath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read APK metadata from '$ApkPath': $($badging -join [Environment]::NewLine)"
    }
    $match = [regex]::Match(($badging -join "`n"), "versionCode='(\d+)'")
    if (-not $match.Success) {
        throw "Could not find versionCode in APK metadata: $ApkPath"
    }
    return [Int64]$match.Groups[1].Value
}

function Get-InstalledPackageVersionCode {
    $dump = @(& $adb -s $Device shell "dumpsys package $package" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect installed package '$package': $($dump -join [Environment]::NewLine)"
    }
    $match = [regex]::Match(($dump -join "`n"), '\bversionCode=(\d+)')
    if (-not $match.Success) {
        return $null
    }
    return [Int64]$match.Groups[1].Value
}

$script:activeCacheHandled = $false

function Invoke-ActiveCachePatch {
    # Last Z may use a downloaded AssetBundle that overrides the copy embedded
    # in the APK.  Patch that active route first; falling through to the APK
    # path would otherwise produce a technically valid but unused patch.
    $cacheStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $cacheOut = Join-Path $OutputRoot ("active_cache_{0}" -f $cacheStamp)
    $cacheOutput = @(& py $activeCacheScript `
        --adb $adb `
        --device $Device `
        --package $package `
        --out-dir $cacheOut 2>&1)
    $cacheExitCode = $LASTEXITCODE
    foreach ($line in $cacheOutput) {
        Write-Host $line
    }

    $cacheText = $cacheOutput -join [Environment]::NewLine
    if ($cacheExitCode -eq 4 -and $cacheText -match 'ACTIVE_CACHE_NOT_FOUND') {
        return
    }
    if ($cacheExitCode -ne 0) {
        throw "Active downloaded chat-bundle patch failed: $cacheText"
    }
    if ($cacheText -match 'ACTIVE_CACHE_PATCH_OK|ACTIVE_CACHE_ALREADY_PATCHED') {
        $script:activeCacheHandled = $true
        return
    }
    throw "Active downloaded chat-bundle updater ended without a verified result: $cacheText"
}

# A normal -Install first patches the exact downloaded bundle currently used
# by the game.  It stops only the Last Z package while atomically swapping
# checksummed files; the APK route remains a fallback for installations that
# do not have downloaded AssetBundles yet.
if ($Install -and -not $Reinstall) {
    Invoke-ActiveCachePatch
    if ($script:activeCacheHandled) {
        Write-Output 'INSTALL_OK (active downloaded chat bundle patched; restart Last Z normally).'
        exit 0
    }
}

function Refresh-InputSplitsFromDevice {
    $remotePaths = @(& $adb -s $Device shell "pm path $package" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not list installed split APKs for '$package': $($remotePaths -join [Environment]::NewLine)"
    }
    $remotePaths = @($remotePaths | ForEach-Object {
        $line = $_.ToString().Trim()
        if ($line -match '^package:(.+)$') { $Matches[1] }
    } | Where-Object { $_ })

    $requiredNames = @('base.apk', 'split_config.arm64_v8a.apk', 'split_install_time_pack.apk')
    $selected = @{}
    foreach ($name in $requiredNames) {
        $matches = @($remotePaths | Where-Object { $_ -match ("/" + [regex]::Escape($name) + '$') })
        if ($matches.Count -ne 1) {
            throw "Expected exactly one installed '$name' split, found $($matches.Count)."
        }
        $selected[$name] = $matches[0]
    }

    $installedVersion = Get-InstalledPackageVersionCode
    if ($null -eq $installedVersion) {
        throw "Package '$package' is not installed on device '$Device'."
    }
    $snapshotStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $snapshotRoot = Join-Path $scriptRoot 'apk\device_snapshots'
    $snapshot = Join-Path $snapshotRoot ("version_{0}_{1}" -f $installedVersion, $snapshotStamp)
    New-Item -ItemType Directory -Path $snapshot -ErrorAction Stop | Out-Null
    foreach ($name in $requiredNames) {
        $destination = Join-Path $snapshot $name
        # adb writes its transfer status to stdout.  Capture it so this helper
        # has exactly one success-pipeline value: the input-path object below.
        $pullOutput = @(& $adb -s $Device pull $selected[$name] $destination 2>&1)
        $pullExitCode = $LASTEXITCODE
        if ($pullExitCode -ne 0) {
            throw "Could not export installed split '$name' to '$snapshot': $($pullOutput -join [Environment]::NewLine)"
        }
        Write-Verbose ($pullOutput -join [Environment]::NewLine)
    }
    # Keep the status line off the success pipeline: the caller receives only
    # the structured three-path object below.
    Write-Host "DEVICE_SPLIT_SNAPSHOT=$snapshot"
    return [pscustomobject]@{
        BaseApk = Join-Path $snapshot 'base.apk'
        ConfigApk = Join-Path $snapshot 'split_config.arm64_v8a.apk'
        PackApk = Join-Path $snapshot 'split_install_time_pack.apk'
    }
}

if ($RefreshFromDevice) {
    $snapshotInputs = Refresh-InputSplitsFromDevice
    $BaseApk = $snapshotInputs.BaseApk
    $ConfigApk = $snapshotInputs.ConfigApk
    $PackApk = $snapshotInputs.PackApk
}

foreach ($inputApk in @($PackApk, $BaseApk, $ConfigApk)) {
    if (-not (Test-Path -LiteralPath $inputApk)) { throw "Missing required APK input: $inputApk" }
}

function Test-InstallVersionCompatibility {
    param([string]$CandidateBaseApk)

    $candidateVersion = Get-ApkVersionCode $CandidateBaseApk
    $installedVersion = Get-InstalledPackageVersionCode
    if ($null -eq $installedVersion) {
        Write-Output "APK_VERSION_CHECK candidate=$candidateVersion installed=not-installed result=compatible"
        return
    }
    if ($installedVersion -le $candidateVersion) {
        Write-Output "APK_VERSION_CHECK candidate=$candidateVersion installed=$installedVersion result=compatible"
        return
    }
    if ($Reinstall) {
        Write-Warning "APK_VERSION_CHECK candidate=$candidateVersion installed=$installedVersion result=reinstall-will-remove-current-package"
        return
    }
    if ($AllowDowngrade) {
        Write-Warning "APK_VERSION_CHECK candidate=$candidateVersion installed=$installedVersion result=explicit-downgrade-requested"
        return
    }

    throw @"
Refusing to build/install an outdated split set: candidate versionCode is $candidateVersion, but device '$Device' has $installedVersion.
Use -RefreshFromDevice to capture the current installed splits into a new apk\device_snapshots folder, then patch that snapshot.
If a downgrade is genuinely intended, rerun with -Install -AllowDowngrade. This does not bypass a signing-key mismatch; use -Reinstall only when you explicitly accept uninstalling the existing app and its internal data.
"@
}

if ($Install -or $PreflightOnly) {
    Test-InstallVersionCompatibility $BaseApk
}
if ($PreflightOnly) {
    if ($RefreshFromDevice) {
        Write-Output 'PREFLIGHT_OK (installed splits were captured; no build, signing, or install was performed).'
    } else {
        Write-Output 'PREFLIGHT_OK (no build, signing, install, or source modification performed).'
    }
    exit 0
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = Join-Path $OutputRoot "build_$stamp"
New-Item -ItemType Directory -Path $out -Force | Out-Null

$updateArgs = @($updateScript, '--pack-apk', $PackApk, '--out-dir', $out)
if ($InitCanary) { $updateArgs += '--init-canary' }
$effectiveManifestVersion = $ManifestVersion
if ($ManifestVersion -eq 'auto') {
    # A running official build has already learned the current CDN manifest
    # version.  Reuse that number so the patched signed package is not
    # immediately superseded by an otherwise-stock downloaded gameres.
    $remoteVersion = & $adb -s $Device shell "cat /sdcard/Android/data/$package/files/AssetBundles/gameres.version 2>/dev/null"
    if ($remoteVersion -match '^(\d+),') {
        $effectiveManifestVersion = $Matches[1]
        Write-Output "Using active CDN gameres version: $effectiveManifestVersion"
    } else {
        $effectiveManifestVersion = $null
        Write-Warning 'No active downloaded gameres.version found; retaining the packaged version.'
    }
}
if ($effectiveManifestVersion) { $updateArgs += @('--manifest-version', $effectiveManifestVersion) }
& py @updateArgs
if ($LASTEXITCODE -ne 0) { throw 'Lua/manifest updater failed.' }

$metadataPath = Join-Path $out 'patch_metadata.json'
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
if ($metadata.details.already_patched -eq $true) {
    Write-Output 'PATCH_ALREADY_INSTALLED (verified PEva capture signature; no APK was signed or installed).'
    exit 0
}
$unsignedPack = Join-Path $out 'split_install_time_pack.unsigned.apk'
$signedPack = Join-Path $out 'split_install_time_pack.signed.apk'
$signedBase = Join-Path $out 'base.signed.apk'
$signedConfig = Join-Path $out 'split_config.arm64_v8a.signed.apk'

& py $replaceScript --apk $PackApk `
    --replace ("{0}={1}" -f $metadata.bundle_entry, $metadata.patched_bundle) `
    --replace ("{0}={1}" -f $metadata.gameres_entry, $metadata.patched_gameres) `
    --replace ("{0}={1}" -f $metadata.gameres_version_entry, $metadata.patched_gameres_version) `
    --output $unsignedPack
if ($LASTEXITCODE -ne 0) { throw 'APK asset replacement failed.' }

function Sign-Apk([string]$InputApk, [string]$OutputApk) {
    & $apksigner sign --ks $keystore --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out $OutputApk $InputApk
    if ($LASTEXITCODE -ne 0) { throw "Signing failed: $InputApk" }
    & $apksigner verify --verbose $OutputApk
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $OutputApk" }
}

Sign-Apk $BaseApk $signedBase
Sign-Apk $ConfigApk $signedConfig
Sign-Apk $unsignedPack $signedPack

Write-Output "BUILD_OK=$out"
Write-Output "METADATA=$metadataPath"
Write-Output "PACK_SHA256=$((Get-FileHash -LiteralPath $signedPack -Algorithm SHA256).Hash)"

if (-not $Install) {
    Write-Output 'Not installed (pass -Install to submit the signed split set to ADB).'
    exit 0
}

$externalBackup = Join-Path $out 'external_backup'
if ($Reinstall) {
    New-Item -ItemType Directory -Path $externalBackup -Force | Out-Null
    & $adb -s $Device pull "/sdcard/Android/data/$package" $externalBackup
    if ($LASTEXITCODE -ne 0) { throw 'External app-file backup failed; refusing uninstall.' }
    & $adb -s $Device uninstall $package
    if ($LASTEXITCODE -ne 0) { throw 'Uninstall failed.' }
    # The complete external-app directory is retained on the host.  This
    # Android image blocks shell/ADB writes to /Android/data/<package> even
    # while uninstalled, so restoration requires device-owner/root access and
    # is deliberately not claimed by this updater.
}

function Install-SignedSplits {
    param([array]$Artifacts)

    # BlueStacks' `pm` does not provide the convenience `install-multiple`
    # command consistently.  Its install session API is stable and is also
    # what was used to validate the runtime split update.
    $createCommand = if ($AllowDowngrade) { 'pm install-create -r -d' } else { 'pm install-create -r' }
    $created = & $adb -s $Device shell $createCommand
    if ($created -notmatch '\[(\d+)\]') {
        throw "Could not create an Android install session: $created"
    }
    $session = $Matches[1]
    $remoteFiles = @()
    $commitSubmitted = $false
    try {
        foreach ($artifact in $Artifacts) {
            $name = Split-Path -Leaf $artifact
            $size = (Get-Item -LiteralPath $artifact).Length
            $remote = "/data/local/tmp/peva-lastz-$stamp-$name"
            $remoteFiles += $remote
            & $adb -s $Device push $artifact $remote
            if ($LASTEXITCODE -ne 0) { throw "ADB push failed: $name" }
            & $adb -s $Device shell "pm install-write -S $size $session $name $remote"
            if ($LASTEXITCODE -ne 0) { throw "Android install-write failed: $name" }
        }
        # Android closes the session during install-commit, even for a failed
        # validation.  Do not call install-abandon after this point: recent
        # Android/BlueStacks images then emit an unrelated SecurityException.
        $commitSubmitted = $true
        $commitOutput = @(& $adb -s $Device shell "pm install-commit $session" 2>&1)
        $commitExitCode = $LASTEXITCODE
        $commitText = $commitOutput -join [Environment]::NewLine
        if ($commitExitCode -ne 0 -or $commitText -match '(?im)^Failure\b') {
            throw "Android install-commit failed for session [$session]: $commitText"
        }
    } catch {
        if (-not $commitSubmitted) {
            # Preserve the original install/push/write failure.  Session
            # cleanup is best-effort and must never obscure it.
            try {
                & $adb -s $Device shell "pm install-abandon $session" 2>$null | Out-Null
            } catch {
                Write-Verbose "Could not abandon Android install session [$session]."
            }
        }
        throw
    } finally {
        # These are unique, script-created temp paths; cleanup never touches
        # app data or the input APKs.
        foreach ($remote in $remoteFiles) {
            & $adb -s $Device shell "rm -f $remote" | Out-Null
        }
    }
}

Install-SignedSplits @($signedBase, $signedConfig, $signedPack)
Write-Output 'INSTALL_OK'
