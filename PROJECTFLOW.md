# 1. Understand the project structure completely.
substack-newsletters-search-system/
├── .env.example                 # Environment variables template
├── .github/                     # GitHub configuration and CI/CD workflows
├── Dockerfile                   # Dockerfile for FastAPI app
├── Makefile                     # Automation commands
├── README.md                    # Project Readme
├── INSTRUCTIONS.md              # Instruction to Follow
├── PROJECTFLOW.md               # Understanding the projectlfow
├── cloudbuild_fastapi.yaml      # Google Cloud Build config for FastAPI
├── deploy_fastapi.sh            # Script to deploy FastAPI to Cloud Run
├── prefect-cloud.yaml           # Prefect Cloud deployment
├── prefect-local.yaml           # Prefect local deployment
├── pyproject.toml               # Python dependencies
├── requirements.txt             # Prefect deployment deps
├── frontend/                    # Gradio UI
├── src/
│   ├── api/                     # FastAPI application
│   │   ├── exceptions/          # Error handlers
│   │   ├── middleware/          # Logging middleware
│   │   ├── models/              # API schemas
│   │   ├── routes/              # Endpoint definitions
│   │   ├── services/            # Business logic
│   │   └── main.py              # App entry point
│   ├── config.py                # Centralized settings
│   ├── configs/                 # Newsletter sources
│   ├── models/                  # Pydantic/SQLAlchemy models
│   ├── infrastructure/          # Infrastructure integrations
│   │   ├── supabase/            # Database setup
│   │   └── qdrant/              # Vector store setup
│   ├── pipelines/               # Prefect flows and tasks
│   │   ├── flows/               # Prefect workflows
│   │   └── tasks/               # Prefect tasks
│   └── utils/                   # Logging and text splitter utils
└── tests/                       # Tests
    ├── conftest.py              # Pytest configuration
    ├── integration/             # Integration tests (DB, pipeline)
    └── unit/                    # Unit tests

# 2. Automation with Makefile check Makefile
The Makefile serves as a command center for all common operations—database setup, data ingestion, deployment, testing, and code quality checks.

Rather than remembering complex command sequences with multiple flags and environment variables, you run simple commands like make supabase-create or make ingest-embeddings-flow.

# Supabase
supabase-create                # Create Supabase database
supabase-delete                # Delete Supabase database

# Qdrant
qdrant-create-collection       # Create Qdrant collection
qdrant-delete-collection       # Delete Qdrant collection
qdrant-create-index            # Create Qdrant index
qdrant-ingest-from-sql         # Ingest data from SQL to Qdrant

# Prefect flows
ingest-rss-articles-flow       # Ingest RSS articles flow
ingest-embeddings-flow         # Ingest embeddings flow

# Prefect deployment
deploy-cloud-flows             # Deploy Prefect flows to Prefect Cloud
deploy-local-flows             # Deploy Prefect flows to Prefect Local Server

# Run services
run-api                        # Run FastAPI application
run-gradio                     # Run Gradio application

# Quality checks
all-check                      # Run all: linting, formatting and type checking
all-fix                        # Run all fix: auto-formatting and linting fixes
clean                          # Clean up cached generated files

# 3. Configuration Management check src/config.py

I used Pydantic Settings for type-safe configuration with environment variable support. This approach provides compile-time validation and clear documentation of required settings, catching configuration errors before they cause runtime failures.

## 3.1 Check .env

When the Settings class loads these enviroment variables (via env_nested_delimiter=”__”), it automatically maps SUPABASE_DB__HOST to settings.supabase_db.host. You get clean namespace separation without verbose variable names, and the structure matches your Python code organization.

## 3.2 Check src/configs/feeds_rss.yaml

Newsletter sources live in a YAML configuration file, which is easier to edit and version control than hardcoding them in Python:

# 4. Supabase: Structured Metadata Storage check src/models/sql_models.py

The Supabase schema needs to balance two competing concerns: normalization principles that reduce data redundancy, and query performance that keeps searches fast.

# 4.1 Creating the database run below command:
# make supabase-create
This command runs the create_db.py script, which checks for existing tables before creating new ones: check src/infrastructure/supabase/create_db.py

# 5. RSS Parsing: Handling Real-World Messiness

Once we have successfully created our database it is time to define the Prefect tasks that will orchestrate in one flow the ingestion of the newsletter articles into the database. We will be using two task: RSS parsing and batch ingestion.

# 5.1. RSS Parsing (check src/pipelines/tasks/fetch_rss.py)
RSS feeds from Substack contain XML content that needs conversion to clean Markdown for better chunking and embedding quality. But the real challenge isn’t the happy path—it’s the edge cases that break naive parsers. Paywalled content, missing fields, and malformed XML all lurk in production RSS feeds.

# 5.2. Batch Ingestion: Optimizing Database Writes (check src/pipelines/tasks/ingest_rss.py)
Now that we’ve successfully parsed RSS feeds and filtered out paywalled content, we need an efficient strategy for writing these articles to the database. Writing them one at a time would work, but it’s slow and puts unnecessary load on Supabase. Let’s look at how batching solves this.
 
# 6. Orchestrating the Pipeline with Prefect Flows (check src/pipelines/flows/rss_ingestion_flow.py)
At this time we’ve built the individual components—database schema, RSS parsing, batch ingestion. Now we need to orchestrate them into a cohesive pipeline. This is where Prefect flows come in, coordinating the execution of tasks while handling errors, retries, and parallel processing.

# 6.1 Running the Flow
You can trigger the RSS ingestion flow using the Makefile command:
make ingest-rss-articles-flow