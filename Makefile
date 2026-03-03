# Detect OS and set Python path accordingly
ifeq ($(OS),Windows_NT)
	PYTHON ?= .venv/Scripts/python.exe
else
	PYTHON ?= .venv/bin/python
endif
CORPUS_DIR ?= data/corpus/fpml_official
CORPUS_REPORT ?= data/corpus/reports/latest.json
CORPUS_REPORT_FX ?= data/corpus/reports/latest_fx.json

.PHONY: test check parse-sample convert-sample corpus-import corpus-check corpus-check-fx

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -q

parse-sample:
	$(PYTHON) -m fpml_cdm parse tests/fixtures/fpml/fx_forward.xml

convert-sample:
	$(PYTHON) -m fpml_cdm convert tests/fixtures/fpml/fx_forward.xml

corpus-import:
	$(PYTHON) scripts/import_fpml_corpus.py --dest $(CORPUS_DIR)

corpus-check:
	$(PYTHON) scripts/run_local_corpus_check.py --corpus $(CORPUS_DIR) --output $(CORPUS_REPORT)

corpus-check-fx:
	$(PYTHON) scripts/run_local_corpus_check.py --corpus $(CORPUS_DIR) --include-path "/fx-derivatives/" --output $(CORPUS_REPORT_FX)

check: test
