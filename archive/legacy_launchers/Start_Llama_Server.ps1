$ErrorActionPreference = 'Stop'
try {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
    $exe = Get-ChildItem -LiteralPath $root -Filter 'llama-server.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if (-not $exe) {
        Write-Host 'Nie znaleziono llama-server.exe.' -ForegroundColor Yellow
        Write-Host 'Wypakuj oficjalne wydanie llama.cpp Windows CUDA do tego folderu (lub podfolderu).'
        exit 1
    }
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Wybierz model GGUF dla llama-server'
    $dialog.Filter = 'Modele GGUF (*.gguf)|*.gguf'
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
    $dialog.InitialDirectory = $root
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host 'Nie wybrano modelu.'
        exit 0
    }
    $model = $dialog.FileName
    $argsList = @('-m', $model, '--host', '127.0.0.1', '--port', '8080', '-ngl', '99', '-c', '4096', '-t', '8', '-b', '128')
    Write-Host "Serwer: $exe" -ForegroundColor Cyan
    Write-Host "Model:  $model" -ForegroundColor Cyan
    Write-Host 'Interfejs: http://127.0.0.1:8080' -ForegroundColor Green
    Write-Host 'Zatrzymywanie serwera: Ctrl+C' -ForegroundColor Gray
    Push-Location (Split-Path -Parent $exe)
    try { & $exe @argsList } finally { Pop-Location }
} catch {
    Write-Host "Blad: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
