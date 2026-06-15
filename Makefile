# Makefile

# Check if .env exists
ifeq (,$(wildcard .env))
$(error .env file is missing at .env. Please create one based on .env.example)
endif

# Load environment variables from .env
include .env

.PHONY: tests mypy clean help ruff-check ruff-check-fix ruff-format ruff-format-fix all-check all-fix


#################################################################################
## Supabase Commands
#################################################################################

supabase-create: ## Create Supabase database
	@echo "Creating Supabase database..."
	uv run python -m src.infrastructure.supabase.create_db

supabase-delete: ## Delete Supabase database
	@echo "Deleting Supabase database..."
	uv run python -m src.infrastructure.supabase.delete_db

#################################################################################
## Qdrant Commands
#################################################################################

qdrant-create-collection: ## Create Qdrant collection
	@echo "Creating Qdrant collection..."
	uv run python -m src.infrastructure.qdrant.create_collection

qdrant-delete-collection: ## Delete Qdrant collection
	@echo "Deleting Qdrant collection..."
	uv run python -m src.infrastructure.qdrant.delete_collection

qdrant-create-index: ## Create Qdrant index
	@echo "Updating HNSW and creating Qdrant indexes..."
	uv run python -m src.infrastructure.qdrant.create_indexes

qdrant-ingest-from-sql: ## Ingest data from SQL to Qdrant
	@echo "Ingesting data from SQL to Qdrant..."
	uv run python -m src.infrastructure.qdrant.ingest_from_sql
	@echo "Data ingestion complete."

#################################################################################
## Prefect Flow Commands
#################################################################################

ingest-rss-articles-flow: ## Ingest RSS articles flow
	@echo "Running ingest RSS articles flow..."
	uv run python -m src.pipelines.flows.rss_ingestion_flow
	@echo "Ingest RSS articles flow completed."

ingest-embeddings-flow: ## Ingest embeddings flow
	@echo "Running ingest embeddings flow..."
	$(if $(FROM_DATE), \
		uv run python -m src.pipelines.flows.embeddings_ingestion_flow --from-date $(FROM_DATE), \
		uv run python -m src.pipelines.flows.embeddings_ingestion_flow)
	@echo "Ingest embeddings flow completed."

#################################################################################
## Prefect Deployment Commands
#################################################################################
deploy-cloud-flows: ## Deploy Prefect flows to Prefect Cloud
	@echo "Deploying Prefect flows to Prefect Cloud..."
	prefect deploy --prefect-file prefect-cloud.yaml
	@echo "Prefect Cloud deployment complete."

deploy-local-flows: ## Deploy Prefect flows to Prefect Local Server
	@echo "Deploying Prefect flows to Prefect Local Server..."
	prefect deploy --prefect-file prefect-local.yaml
	@echo "Prefect Local deployment complete."

#################################################################################
## Recreate Commands
#################################################################################

recreate-supabase: supabase-delete supabase-create ## Recreate Supabase resources

recreate-qdrant: qdrant-delete-collection qdrant-create-collection ## Recreate Qdrant resources

recreate-all: supabase-delete qdrant-delete-collection supabase-create qdrant-create-collection ## Recreate Qdrant and Supabase resources


#################################################################################
## FastAPI Commands
#################################################################################

run-api: ## Run FastAPI application
	@echo "Starting FastAPI application..."
	uv run python -m src.api.main
	@echo "FastAPI application stopped."

#################################################################################
## Gradio Commands
#################################################################################

run-gradio: ## Run Gradio application
	@echo "Starting Gradio application..."
	uv run python -m frontend.app
	@echo "Gradio application stopped."

#################################################################################
## Testing Commands
#################################################################################

unit-tests: ## Run all unit tests
	@echo "Running all unit tests..."
	uv run pytest tests/unit
	@echo "All unit tests completed."

integration-tests: ## Run all integration tests
	@echo "Running all integration tests..."
	uv run pytest tests/integration
	@echo "All integration tests completed."

all-tests: ## Run all tests
	@echo "Running all tests..."
	uv run pytest
	@echo "All tests completed."

################################################################################
## Pre-commit Commands
################################################################################

pre-commit-run: ## Run pre-commit hooks
	@echo "Running pre-commit hooks..."
	pre-commit run --all-files
	@echo "Pre-commit checks complete."

################################################################################
## Linting
################################################################################

# Linting (just checks)
ruff-check: ## Check code lint violations (--diff to show possible changes)
	@echo "Checking Ruff formatting..."
	uv run ruff check .
	@echo "Ruff lint checks complete."

ruff-check-fix: ## Auto-format code using Ruff
	@echo "Formatting code with Ruff..."
	uv run ruff check . --fix --exit-non-zero-on-fix
	@echo "Formatting complete."

################################################################################
## Formatting
################################################################################

# Formatting (just checks)
ruff-format: ## Check code format violations (--diff to show possible changes)
	@echo "Checking Ruff formatting..."
	uv run ruff format . --check
	@echo "Ruff format checks complete."

ruff-format-fix: ## Auto-format code using Ruff
	@echo "Formatting code with Ruff..."
	uv run ruff format .
	@echo "Formatting complete."

#################################################################################
## Static Type Checking
#################################################################################

mypy: ## Run MyPy static type checker
	@echo "Running MyPy static type checker..."
	uv run mypy
	@echo "MyPy static type checker complete."

################################################################################
## Cleanup
################################################################################

clean: ## Clean up cached generated files
	@echo "Cleaning up generated files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Cleanup complete."

################################################################################
## Composite Commands
################################################################################

all-check: ruff-format ruff-check clean ## Run all: linting, formatting and type checking

all-fix: ruff-format-fix ruff-check-fix mypy clean ## Run all fix: auto-formatting and linting fixes

################################################################################
## Help
################################################################################

help: ## Display this help message
	@echo Available targets:
	@echo.
	@echo supabase-create
	@echo supabase-delete
	@echo qdrant-create-collection
	@echo qdrant-delete-collection
	@echo qdrant-create-index
	@echo qdrant-ingest-from-sql
	@echo ingest-rss-articles-flow
	@echo ingest-embeddings-flow
	@echo deploy-cloud-flows
	@echo deploy-local-flows
	@echo recreate-supabase
	@echo recreate-qdrant
	@echo recreate-all
	@echo run-api
	@echo run-gradio
	@echo unit-tests
	@echo integration-tests
	@echo all-tests
	@echo pre-commit-run
	@echo ruff-check
	@echo ruff-check-fix
	@echo ruff-format
	@echo ruff-format-fix
	@echo mypy
	@echo clean
	@echo all-check
	@echo all-fix

.DEFAULT_GOAL := help
