<#!
.SYNOPSIS
  Downloads a static ffmpeg build (Windows) locally under tools/ffmpeg and updates a local PATH helper file.
.DESCRIPTION
  - Downloads latest gyan.dev release (full build) by default.
  - Extracts to tools/ffmpeg
  - Writes tools/ffmpeg/PATH_ADD.txt with instructions
  - Optionally sets current session PATH if -UseSession is provided.
.PARAMETER UseSession
  If provided, prepends tools/ffmpeg to current PowerShell session's PATH.
#>
param(
  [switch]$UseSession,
  [string]$Url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $root '..') | Select-Object -ExpandProperty Path
$toolsDir = Join-Path $repoRoot 'tools'
$ffmpegDir = Join-Path $toolsDir 'ffmpeg'

if (!(Test-Path $toolsDir)) { New-Item -ItemType Directory -Path $toolsDir | Out-Null }
if (Test-Path $ffmpegDir) {
  Write-Host "[ffmpeg] Existing directory found at $ffmpegDir" -ForegroundColor Yellow
} else {
  New-Item -ItemType Directory -Path $ffmpegDir | Out-Null
}

$tempZip = Join-Path $ffmpegDir 'ffmpeg.zip'
Write-Host "[ffmpeg] Downloading: $Url" -ForegroundColor Cyan
Invoke-WebRequest -Uri $Url -OutFile $tempZip

Write-Host "[ffmpeg] Extracting..." -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($tempZip, $ffmpegDir, $true)
Remove-Item $tempZip -Force

# Find first bin directory
$binPath = Get-ChildItem -Path $ffmpegDir -Recurse -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'bin/ffmpeg.exe') } | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'bin' }
if (-not $binPath) {
  # fallback direct search
  $ff = Get-ChildItem -Path $ffmpegDir -Recurse -Filter ffmpeg.exe | Select-Object -First 1
  if ($ff) { $binPath = Split-Path -Parent $ff.FullName }
}
if (-not $binPath) { throw 'Could not locate ffmpeg.exe after extraction' }

Write-Host "[ffmpeg] Located bin: $binPath" -ForegroundColor Green

# Write helper file
$pathDoc = @()
$pathDoc += "Add this to your PATH (PowerShell profile or system settings):"
$pathDoc += $binPath
$pathDoc += ''
$pathDoc += 'Current session addition command:'
$pathDoc += "$Env:PATH = '$binPath;' + $Env:PATH"
$pathDoc += ''
$pathFile = Join-Path $ffmpegDir 'PATH_ADD.txt'
$pathDoc | Out-File -FilePath $pathFile -Encoding UTF8
Write-Host "[ffmpeg] Wrote helper: $pathFile" -ForegroundColor Green

if ($UseSession) {
  $env:PATH = "$binPath;$env:PATH"
  Write-Host "[ffmpeg] Added to current session PATH" -ForegroundColor Green
}

# Sanity check
$ver = & "$binPath/ffmpeg.exe" -version 2>$null | Select-Object -First 1
Write-Host "[ffmpeg] Version: $ver" -ForegroundColor Green
