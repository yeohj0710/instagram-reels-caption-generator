$ErrorActionPreference = "Stop"

$DevRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProgramFilesDir = Split-Path -Parent $DevRoot
$RepoRoot = Split-Path -Parent $ProgramFilesDir
$ProgramDirName = -join ([char[]](0xD504, 0xB85C, 0xADF8, 0xB7A8, 0x20, 0xAD6C, 0xC131, 0x20, 0xD30C, 0xC77C))
$DevDirName = -join ([char[]](0xAC1C, 0xBC1C, 0x20, 0xD30C, 0xC77C))
$OutputDirName = -join ([char[]](0xC0DD, 0xC131, 0xB41C, 0x20, 0xCEA1, 0xC158))
$TrainingDirName = -join ([char[]](0xD559, 0xC2B5, 0xC6A9, 0x20, 0xB370, 0xC774, 0xD130))
$GuideFileName = (-join ([char[]](0xC0AC, 0xC6A9, 0xC124, 0xBA85, 0xC11C))) + ".html"
$DownloadZipImageName = "github-download-zip.png"
$ExeBaseName = -join ([char[]](0xB9B4, 0xC2A4, 0x20, 0xCEA1, 0xC158, 0x20, 0xC0DD, 0xC131, 0xAE30))
$ExeFileName = $ExeBaseName + ".exe"
Set-Location -LiteralPath $DevRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt pytest
& ".\.venv\Scripts\python.exe" -m pytest

$PythonBase = (& ".\.venv\Scripts\python.exe" -c "import sys; print(sys.base_prefix)").Trim()
$ReferenceRuntimeDirs = @(
    (Join-Path "C:\dev\media-summary-note-generator" $ProgramDirName),
    (Join-Path "C:\dev\youtube-instagram-media-extractor" $ProgramDirName)
)

$TclTkDataNames = @("_tcl_data", "_tk_data", "tcl8")
$TclTkSourceCandidates = @{
    "_tcl_data" = @((Join-Path $PythonBase "tcl\tcl8.6"))
    "_tk_data" = @((Join-Path $PythonBase "tcl\tk8.6"))
    "tcl8" = @((Join-Path $PythonBase "tcl\tcl8"))
}
foreach ($RuntimeDir in $ReferenceRuntimeDirs) {
    foreach ($Name in $TclTkDataNames) {
        $TclTkSourceCandidates[$Name] += (Join-Path $RuntimeDir $Name)
    }
}

function Copy-TclTkDataFolders {
    param([string]$DestinationRuntimeDir)

    foreach ($Name in $TclTkDataNames) {
        $SourceDir = $null
        foreach ($Candidate in $TclTkSourceCandidates[$Name]) {
            if (Test-Path -LiteralPath $Candidate) {
                $SourceDir = (Resolve-Path -LiteralPath $Candidate).Path
                break
            }
        }
        if (-not $SourceDir) {
            throw "Required Tcl/Tk data folder was not found: $Name"
        }

        $TargetDir = Join-Path $DestinationRuntimeDir $Name
        if (Test-Path -LiteralPath $TargetDir) {
            Remove-Item -LiteralPath $TargetDir -Recurse -Force
        }
        Copy-Item -LiteralPath $SourceDir -Destination $TargetDir -Recurse -Force
    }
}

$BuildDir = Join-Path $DevRoot "build"
$DistDir = Join-Path $DevRoot "dist"
if ([System.IO.Directory]::Exists($BuildDir)) {
    [System.IO.Directory]::Delete($BuildDir, $true)
}
if ([System.IO.Directory]::Exists($DistDir)) {
    [System.IO.Directory]::Delete($DistDir, $true)
}

& ".\.venv\Scripts\python.exe" -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name $ExeBaseName `
    --icon "assets\caption-generator.ico" `
    --contents-directory $ProgramDirName `
    --add-data "assets\caption-generator.ico;assets" `
    --add-data "assets\caption-generator.png;assets" `
    --collect-all customtkinter `
    --collect-all ctypes `
    --collect-all openai `
    --collect-all PIL `
    --collect-all certifi `
    --collect-binaries imageio_ffmpeg `
    --hidden-import ctypes._layout `
    --hidden-import yt_dlp `
    "src\reels_caption_generator\__main__.py"

$BuiltAppDir = Join-Path $DevRoot ("dist\" + $ExeBaseName)
$BuiltExe = Join-Path $BuiltAppDir $ExeFileName
$BuiltRuntimeDir = Join-Path $BuiltAppDir $ProgramDirName

if (-not (Test-Path -LiteralPath $BuiltExe)) {
    throw "Built exe was not found: $BuiltExe"
}
if (-not (Test-Path -LiteralPath $BuiltRuntimeDir)) {
    throw "Built runtime folder was not found: $BuiltRuntimeDir"
}

Copy-TclTkDataFolders -DestinationRuntimeDir $BuiltRuntimeDir
Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $RepoRoot $ExeFileName) -Force

New-Item -ItemType Directory -Force -Path $ProgramFilesDir | Out-Null
$DevRootResolved = (Resolve-Path -LiteralPath $DevRoot).Path
Get-ChildItem -LiteralPath $ProgramFilesDir -Force | ForEach-Object {
    try {
        $ItemPath = (Resolve-Path -LiteralPath $_.FullName -ErrorAction Stop).Path
    } catch {
        return
    }
    if ($ItemPath -ne $DevRootResolved -and $_.Name -ne $DevDirName) {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Get-ChildItem -LiteralPath $BuiltRuntimeDir -Force | ForEach-Object {
    if ($_.Name -eq "desktop.ini" -or -not (Test-Path -LiteralPath $_.FullName)) {
        return
    }
    Copy-Item -LiteralPath $_.FullName -Destination $ProgramFilesDir -Recurse -Force
}

$DownloadZipImageSource = Join-Path (Join-Path $DevRoot "assets") $DownloadZipImageName
if (Test-Path -LiteralPath $DownloadZipImageSource) {
    Copy-Item -LiteralPath $DownloadZipImageSource -Destination (Join-Path $ProgramFilesDir $DownloadZipImageName) -Force
}

New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $OutputDirName) | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $TrainingDirName) | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $GuideFileName))) {
    throw "HTML guide file was not found: $(Join-Path $RepoRoot $GuideFileName)"
}

Write-Host ""
Write-Host "Done:"
Write-Host ("  " + $ExeFileName)
Write-Host ("  " + $GuideFileName)
Write-Host ("  " + $OutputDirName + "\")
Write-Host ("  " + $TrainingDirName + "\")
Write-Host ("  " + $ProgramDirName + "\")
