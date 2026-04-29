.PHONY: up down logs reset dev-core dev-dashboard build agent agent-mock agent-drive test

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

reset:
	curl -X POST http://localhost:8080/api/demo/reset

dev-core:
	cd services/core && go run ./cmd/server

dev-dashboard:
	cd web/dashboard && npm run dev

build:
	docker compose build

agent:
	cd agent && python3 picar_telemetry_agent.py

agent-mock:
	cd agent && python3 picar_telemetry_agent.py --mock

agent-drive:
	cd agent && python3 picar_autonomous.py

test:
	cd services/core && go test ./...

test-e2e:
	bash scripts/test_e2e.sh
