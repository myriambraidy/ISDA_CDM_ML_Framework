# Detect OS and set Python path accordingly
ifeq ($(OS),Windows_NT)
	PYTHON ?= .venv/Scripts/python.exe
else
	PYTHON ?= .venv/bin/python
endif
CORPUS_DIR ?= data/corpus/fpml_official
CORPUS_REPORT ?= data/corpus/reports/latest.json
CORPUS_REPORT_FX ?= data/corpus/reports/latest_fx.json
ROSETTA_JAR ?= rosetta-validator/target/rosetta-validator-1.0.0.jar

.PHONY: test check parse-sample convert-sample corpus-import corpus-check corpus-check-fx rosetta-build validate-rosetta-sample

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

rosetta-build:
	cd rosetta-validator && mvn package -q -DskipTests

validate-rosetta-sample: $(ROSETTA_JAR)
	$(PYTHON) -m fpml_cdm validate-rosetta tests/fixtures/expected/fx_forward_cdm.json

$(ROSETTA_JAR):
	$(MAKE) rosetta-build

check: test
