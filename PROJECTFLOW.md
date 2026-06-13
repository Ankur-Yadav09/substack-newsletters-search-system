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

# 7. Text Chunking:  Breaking Articles into Searchable Units
# check src/utils/text_splitter.py
The default configuration uses 4,000 characters (600 - 800 words) per chunk with 200 character overlap.

# 8. Qdrant Collection: Configuring Hybrid Vector Storage
With text properly chunked, we need a storage infrastructure that handles both dense and sparse vectors efficiently.

# check src/infrastructure/qdrant/qdrant_vectorstore.py

# 9. Embedding Generation: Dense and Sparse Vectors
Now that our collection is configured, it’s time to generate embeddings that translate each chunk into a vector representation for search. 

# check src/infrastructure/qdrant/qdrant_vectorstore.py

# 9.1 Dense Embeddings with Fastembed
The default dense embedding model is BAAI/bge-base-en from Fastembed, producing 768-dimensional vectors.

# 9.2 Sparse Embeddings with BM25
Sparse embeddings use Qdrant’s BM25 model, generating vectors where non-zero values represent term importance.

# 10. Prefect Orchestration: The Embeddings Ingestion Flow
With the core ingestion logic implemented in AsyncQdrantVectorStore, we need Prefect orchestration to run it reliably, handle failures gracefully, and support incremental updates.

# 10.1 The Ingestion Task
The ingest_qdrant task wraps the vectorstore’s ingestion method with Prefect’s retry and logging capabilities:
# check src/pipelines/tasks/ingest_embeddings.py

# 10.2 The Orchestration Flow
The qdrant_ingest_flow coordinates the entire process, determining the appropriate date range and invoking the ingestion task.
# check src/pipelines/flows/embeddings_ingestion_flow.py

# 11. Running the Embeddings Ingestion Pipeline

# 11.1 Initial Collection Setup
Start by creating the Qdrant collection:
make qdrant-create-collection

# 11.2 Bulk Ingestion
Next, ingest all articles from Supabase:
make ingest-embeddings-flow

# 11.3 Enable Indexing
After the bulk upload completes, activate HNSW and payload indexes:
make qdrant-create-indexes

# 11.4 Incremental Updates
For routine updates after newsletters publish new content, run the flow with date filtering:
##Ingest only articles published after January 1, 2025
make ingest-embeddings-flow FROM_DATE=2025-01-01

# 11.5 Combining both Flows: RSS Feed and Embeddings
we saw the first flow to ingest raw articles into Supabase, and in this lesson, we have seen how to ingest those articles from Supabase to Qdrant. Now we can combine both in a single deployment file using Prefect YAML files.
# check prefect-cloud.yaml

# 11.6 To deploy flows, use the Makefile commands or the local commands for individual flows available in the README.md file:

# Deploy to Prefect Cloud
make deploy-cloud-flows

# Deploy to local Prefect server
make deploy-local-flows

# 12. FastAPI Application Structure: Separation of Concerns
The FastAPI application can be found in the api folder and follows a layered architecture where each component has a single, well-defined responsibility:

# 12.1 Routes check src\api\routes
It's define HTTP endpoints and parameter validation. They receive requests, delegate work to services, and format responses. Routes never contain business logic—they’re thin adapters between HTTP and your application core. The project implements two routes.

Health Route: checks the API readiness and availability

Search Route: checks the request and routes it to either get a list of unique titles or a response generated by an LLM. 

# 12.2 Services check src\api\services
It's implement business logic. The implementation covers two services:

Search Service: queries Qdrant and handles deduplication. It also provides the unique titles if the user has selected that option.

Generation Service: orchestrates LLM calls and manages streaming and non-streaming responses.

# 12.3 Providers check src\api\services\providers
It's abstract LLM implementations with different providers. Each provider implements the same interface: non-streaming and streaming responses. Swapping providers requires changing one configuration value, not rewriting code.

# 12.2.1 Search Service: Vector Queries with Metadata Filtering check \src\api\services\search_service.py
The search service translates user queries into vector searches, applies metadata filters, and returns ranked results. Two core functions handle this: query_with_filters for general searches using an LLM and query_unique_titles for title-only searches.

# 12.2.2 Generation Service: Orchestrating Multi-Provider LLM Calls check \src\api\services\generation_service.py
The generation service sits between routes and LLM providers, handling prompt construction, provider selection, evaluation (optional), and response streaming. Two functions form the interface: generate_answer for non-streaming responses and get_streaming_function for streaming.

# 13. Prompt Construction check src\api\services\providers\utils\prompts.py
The build_research_prompt function constructs a detailed prompt that includes the user query, all retrieved context chunks, and explicit formatting instructions. The prompt enforces Markdown output, citation requirements, and token limits.

# 14. Provider Selection check src\api\models\provider_models.py
The provider selection uses a registry pattern defined in models/provider_models.py. The registry maps provider names to model configurations.

# 15. Evaluation check src\api\services\providers\utils\evaluation_metrics.py
As mentioned above, the evaluation is only implemented in the OpenRouter function, and the logic resides in the evaluation_metrics.py file. We use G-Eval, an automated evaluation framework that uses language models themselves as judges.

# 16. Running the Application
With all the routes, services, and providers now wired together, the last step is to run the FastAPI application itself.

# Making Requests
With all these components together, you can now start the backend locally and test it with a few requests

# Run this command to start the application:
make run-api