.PHONY: install contract-test integration-test acceptance-test demo-quality demo-acceptance demo-start e2e benchmark benchmark-repeat abcd-benchmark final-acceptance

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

benchmark:
	python tools/run_closed_loop_benchmark.py --mode baseline --output reports/closed_loop_benchmark_baseline.json

benchmark-repeat:
	python tools/run_closed_loop_benchmark.py --mode baseline --repeats 3 --output reports/closed_loop_benchmark_baseline_repeat3.json

abcd-benchmark:
	python tools/run_closed_loop_benchmark.py --manifest testdata/benchmark/abcd_closed_loop_v1.json --mode baseline --repeats 1 --output reports/abcd_closed_loop_v1_baseline.json

final-acceptance:
	python tools/run_final_acceptance.py --offline-repeats 3 --output reports/final_acceptance_report.json
