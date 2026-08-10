.PHONY: contract-test integration-test e2e

contract-test:
	python -c "import json; from pathlib import Path; [json.loads(p.read_text(encoding='utf-8')) for p in Path('contracts').rglob('*.json')]; print('contract JSON syntax: OK')"

integration-test:
	pytest tests/integration -q

e2e:
	pytest tests/e2e -q
