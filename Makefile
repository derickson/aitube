.PHONY: init docker-stop docker-start docker-redeploy run dev stop test work-build work-run work-stop

WORK_IMAGE ?= aitube-work:local
WORK_PORT ?= 8104

init:
	cd frontend && npm install
	@mkdir -p ~/.venvs
	@if [ ! -d ~/.venvs/aitube ]; then uv venv ~/.venvs/aitube; fi
	@if [ ! -L .venv ]; then ln -s ~/.venvs/aitube .venv; fi
	uv sync

docker-stop:
	docker compose stop

docker-start:
	docker compose up -d

docker-redeploy: stop
	docker compose stop
	docker compose build
	docker compose up -d

# Single-container "work" image (root Dockerfile). work-run reads .env.work
# (copy .env.work.example) and serves http://localhost:$(WORK_PORT)/aitube/
work-build:
	docker build -t $(WORK_IMAGE) .

work-run: work-build
	-@docker rm -f aitube-work >/dev/null 2>&1
	docker run -d --name aitube-work --env-file .env.work -p $(WORK_PORT):8000 $(WORK_IMAGE)
	@echo "Work deployment: http://localhost:$(WORK_PORT)/aitube/"

work-stop:
	-docker rm -f aitube-work

run: init
	@echo "Starting backend on :3103 and frontend on :8103..."
	cd frontend && npm run build && nohup npx vite preview --host 0.0.0.0 --port 8103 > /tmp/aitube-frontend.log 2>&1 &
	nohup uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 > /tmp/aitube-backend.log 2>&1 &
	@echo "Logs: /tmp/aitube-backend.log, /tmp/aitube-frontend.log"

dev: init
	@echo "Starting dev servers with hot reload..."
	cd frontend && nohup npx vite --host 0.0.0.0 --port 8103 > /tmp/aitube-frontend.log 2>&1 &
	nohup uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 --reload > /tmp/aitube-backend.log 2>&1 &
	@echo "Logs: /tmp/aitube-backend.log, /tmp/aitube-frontend.log"

stop:
	@echo "Stopping local servers..."
	@for port in 3103 8103; do \
		pids=$$(lsof -ti tcp:$$port -sTCP:LISTEN 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  port $$port: TERM $$pids"; \
			kill $$pids 2>/dev/null || true; \
		fi; \
	done
	@sleep 1
	@for port in 3103 8103; do \
		pids=$$(lsof -ti tcp:$$port -sTCP:LISTEN 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  port $$port: KILL $$pids"; \
			kill -9 $$pids 2>/dev/null || true; \
		fi; \
	done
	-@pkill -f "uvicorn backend.app.main:app" 2>/dev/null; true
	-@pkill -f "vite.*--port 8103" 2>/dev/null; true

test:
	uv run pytest -v
