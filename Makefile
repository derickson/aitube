.PHONY: init docker-stop docker-start docker-redeploy run dev stop

init:
	cd frontend && npm install
	uv sync

docker-stop:
	docker compose stop

docker-start:
	docker compose up -d

docker-redeploy: stop
	docker compose stop
	docker compose build
	docker compose up -d

run:
	@echo "Starting backend on :3103 and frontend on :8103..."
	cd frontend && npm run build && nohup npx vite preview --host 0.0.0.0 --port 8103 > /tmp/aitube-frontend.log 2>&1 &
	nohup uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 > /tmp/aitube-backend.log 2>&1 &
	@echo "Logs: /tmp/aitube-backend.log, /tmp/aitube-frontend.log"

dev:
	@echo "Starting dev servers with hot reload..."
	cd frontend && nohup npx vite --host 0.0.0.0 --port 8103 > /tmp/aitube-frontend.log 2>&1 &
	nohup uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 3103 --reload > /tmp/aitube-backend.log 2>&1 &
	@echo "Logs: /tmp/aitube-backend.log, /tmp/aitube-frontend.log"

stop:
	@echo "Stopping local servers..."
	-pkill -f "uvicorn backend.app.main:app"
	-pkill -f "vite.*--port 8103"
