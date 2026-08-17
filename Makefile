.PHONY: install contract-test integration-test acceptance-test demo-quality demo-acceptance demo-start e2e

install:
	python -m pip install -r requirements.txt

contract-test:
	python -c "import json; from pathlib import Path; [json.loads(p.read_text(encoding='utf-8')) for p in Path('contracts').rglob('*.json')]; print('contract JSON syntax: OK')"

integration-test:
	python -m unittest discover -s tests/integration -t . -v

acceptance-test:
	python -m unittest tests.e2e.test_closed_loop_acceptance -v

demo-quality:
	python -m unittest tests.e2e.test_demo_scenarios tests.e2e.test_demo_quality tests.e2e.test_demo_http -v

demo-acceptance:
	python -m unittest discover -s tests -t . -q

demo-start:
	powershell -ExecutionPolicy Bypass -File demo/start_demo.ps1

e2e:
	python -m unittest discover -s tests/e2e -t . -v
