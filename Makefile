.PHONY: up down logs gen test lint hooks

up: ## Start the local stack (core, website, postgres, redis, mailpit, minio)
	docker compose -f tools/local/docker-compose.yml up -d

down: ## Stop the local stack
	docker compose -f tools/local/docker-compose.yml down

logs: ## Tail logs for the local stack
	docker compose -f tools/local/docker-compose.yml logs -f

gen: ## Regenerate the API client from packages/contracts/openapi.yaml
	pnpm gen

test: ## Run tests across all workspaces
	pnpm test

lint: ## Lint across all workspaces
	pnpm lint

hooks: ## Enable the repo git hooks (branch and commit message checks)
	git config core.hooksPath .githooks
