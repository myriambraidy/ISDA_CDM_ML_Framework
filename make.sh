#!/bin/bash
# Bash make replacement for Windows (Git Bash/WSL)

TARGET="${1:-help}"

# Detect Python path
if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python"
fi

CORPUS_DIR="${CORPUS_DIR:-data/corpus/fpml_official}"
CORPUS_REPORT="${CORPUS_REPORT:-data/corpus/reports/latest.json}"
CORPUS_REPORT_FX="${CORPUS_REPORT_FX:-data/corpus/reports/latest_fx.json}"

case "$TARGET" in
    test|check)
        "$PYTHON" -m unittest discover -s tests -p "test_*.py" -q
        ;;
    parse-sample)
        "$PYTHON" -m fpml_cdm parse tests/fixtures/fpml/fx_forward.xml
        ;;
    convert-sample)
        "$PYTHON" -m fpml_cdm convert tests/fixtures/fpml/fx_forward.xml
        ;;
    corpus-import)
        "$PYTHON" scripts/import_fpml_corpus.py --dest "$CORPUS_DIR"
        ;;
    corpus-check)
        "$PYTHON" scripts/run_local_corpus_check.py --corpus "$CORPUS_DIR" --output "$CORPUS_REPORT"
        ;;
    corpus-check-fx)
        "$PYTHON" scripts/run_local_corpus_check.py --corpus "$CORPUS_DIR" --include-path "/fx-derivatives/" --output "$CORPUS_REPORT_FX"
        ;;
    help|*)
        echo "Available targets:"
        echo "  test              - Run unit tests"
        echo "  check             - Alias for test"
        echo "  parse-sample      - Parse sample FpML file"
        echo "  convert-sample    - Convert sample FpML file"
        echo "  corpus-import     - Import FpML corpus"
        echo "  corpus-check      - Check corpus conversion"
        echo "  corpus-check-fx   - Check FX derivatives only"
        echo ""
        echo "Usage: ./make.sh <target>"
        if [ "$TARGET" != "help" ]; then
            exit 1
        fi
        ;;
esac
