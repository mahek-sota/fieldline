PY   := .venv/bin/python
PORT := 8742
API  := http://127.0.0.1:$(PORT)
SEED := 7

.PHONY: demo serve static test clean

.venv/done: requirements.txt
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt
	touch .venv/done

# Simulate a 5 day site, push the events through the flaky uploader into the
# API, save the report for the static page, and print it.
demo: .venv/done
	@if lsof -nP -iTCP:$(PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
	  echo "port $(PORT) is already in use; run with: make demo PORT=<free port>"; exit 1; fi
	rm -f data/server.db data/outbox.db
	$(PY) sim.py --seed $(SEED)
	FIELDLINE_DB=data/server.db .venv/bin/uvicorn api.main:app --port $(PORT) --log-level warning & \
	  echo $$! > data/api.pid; \
	  for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf $(API)/health >/dev/null && break; sleep 0.5; done; \
	  $(PY) edge.py --api $(API) || { kill `cat data/api.pid`; exit 1; }; \
	  curl -sf $(API)/report > web/report.json; \
	  kill `cat data/api.pid`; rm -f data/api.pid
	$(PY) summary.py web/report.json
	$(PY) build_static.py web/report.json dist/index.html
	@echo "shareable page: dist/index.html (open it directly or deploy it)"

# One self-contained page (report embedded) to deploy or share as a single file.
static: web/report.json
	$(PY) build_static.py web/report.json dist/index.html

web/report.json:
	$(MAKE) demo

serve: .venv/done
	FIELDLINE_DB=data/server.db .venv/bin/uvicorn api.main:app --port $(PORT)

test: .venv/done
	$(PY) -m pytest -q tests

clean:
	rm -rf data dist web/report.json
