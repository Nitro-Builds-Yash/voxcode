<#
.SYNOPSIS
    voxcode launcher for Windows PowerShell
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "       Starting VoxCode Engine            " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check if .env exists
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Write-Host "Creating default .env from .env.example..." -ForegroundColor Yellow
        Copy-Item ".env.example" ".env"
    }
}

# Start backend server
Write-Host "Launching FastAPI backend server at http://127.0.0.1:8000 ..." -ForegroundColor Green
python -m backend.main
