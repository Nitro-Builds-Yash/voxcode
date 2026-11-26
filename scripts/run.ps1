<#
.SYNOPSIS
    Start VoxCode from a source checkout (Windows PowerShell).
#>
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Write-Host "Creating .env from .env.example" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

Write-Host "Starting VoxCode at http://127.0.0.1:8000" -ForegroundColor Green
python -m voxcode serve
