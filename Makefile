.PHONY: install worker server stop call test test-live lint
install:
	uv venv --python 3.12 .venv && . .venv/bin/activate && uv pip install -e ".[dev]" && python -m supplier_caller.agent download-files
worker:             ## start the caller agent (LiveKit worker)
	. .venv/bin/activate && python -m supplier_caller.agent start
server:             ## UI + API + queue consumer on http://localhost:8000
	. .venv/bin/activate && python -m supplier_caller.server
stop:               ## hard-stop worker + server (LiveKit workers drain on SIGTERM, so use -9)
	-pkill -9 -f "^python -m supplier_caller"
call:               ## make call            (dials the supplier's number)   ·   make call PHONE=+1...  (dial another number)
	. .venv/bin/activate && python -m supplier_caller.run_call $(if $(PHONE),--phone $(PHONE),)
test:
	. .venv/bin/activate && pytest -q -m "not live"
test-live:
	. .venv/bin/activate && pytest -q -m live
lint:
	. .venv/bin/activate && ruff check supplier_caller tests
