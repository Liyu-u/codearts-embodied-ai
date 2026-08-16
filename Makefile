.PHONY: contract-test integration-test e2e

contract-test:
	python -c "import json; from pathlib import Path; [json.loads(p.read_text(encoding='utf-8')) for p in Path('contracts').rglob('*.json')]; print('contract JSON syntax: OK')"

integration-test:
	python -m unittest discover -s tests/integration -t . -v

e2e:
	python -m unittest discover -s tests/e2e -t . -v
