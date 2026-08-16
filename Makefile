.PHONY: contract-test integration-test e2e test

contract-test:
	python -m unittest discover -s tests/contract -t . -v

integration-test:
	python -m unittest discover -s tests/integration -t . -v

e2e:
	python -m unittest discover -s tests/e2e -t . -v

test:
	python -m unittest discover -s tests -t . -v
