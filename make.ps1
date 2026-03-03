# PowerShell make replacement for Windows
param(
    [Parameter(Position=0)]
    [string]$Target = "help"
)

$PYTHON = if (Test-Path ".venv/Scripts/python.exe") { ".venv/Scripts/python.exe" } else { "python" }
$CORPUS_DIR = $env:CORPUS_DIR
if (-not $CORPUS_DIR) { $CORPUS_DIR = "data/corpus/fpml_official" }

$CORPUS_REPORT = $env:CORPUS_REPORT
if (-not $CORPUS_REPORT) { $CORPUS_REPORT = "data/corpus/reports/latest.json" }

$CORPUS_REPORT_FX = $env:CORPUS_REPORT_FX
if (-not $CORPUS_REPORT_FX) { $CORPUS_REPORT_FX = "data/corpus/reports/latest_fx.json" }

switch ($Target) {
    "test" {
        & $PYTHON -m unittest discover -s tests -p "test_*.py" -q
    }
    "check" {
        & $PYTHON -m unittest discover -s tests -p "test_*.py" -q
    }
    "parse-sample" {
        & $PYTHON -m fpml_cdm parse tests/fixtures/fpml/fx_forward.xml
    }
    "convert-sample" {
        & $PYTHON -m fpml_cdm convert tests/fixtures/fpml/fx_forward.xml
    }
    "corpus-import" {
        & $PYTHON scripts/import_fpml_corpus.py --dest $CORPUS_DIR
    }
    "corpus-check" {
        & $PYTHON scripts/run_local_corpus_check.py --corpus $CORPUS_DIR --output $CORPUS_REPORT
    }
    "corpus-check-fx" {
        & $PYTHON scripts/run_local_corpus_check.py --corpus $CORPUS_DIR --include-path "/fx-derivatives/" --output $CORPUS_REPORT_FX
    }
    "help" {
        Write-Host "Available targets:"
        Write-Host "  test              - Run unit tests"
        Write-Host "  check             - Alias for test"
        Write-Host "  parse-sample      - Parse sample FpML file"
        Write-Host "  convert-sample    - Convert sample FpML file"
        Write-Host "  corpus-import     - Import FpML corpus"
        Write-Host "  corpus-check      - Check corpus conversion"
        Write-Host "  corpus-check-fx   - Check FX derivatives only"
        Write-Host ""
        Write-Host "Usage: .\make.ps1 <target>"
    }
    default {
        Write-Host "Unknown target: $Target"
        Write-Host "Run '.\make.ps1 help' for available targets"
        exit 1
    }
}
