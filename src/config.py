import os
from typing import ClassVar

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.models.article_models import FeedItem


# -----------------------------
# Supabase database settings
# -----------------------------
class SupabaseDBSettings(BaseModel):
    table_name: str = Field(
        default="substack_articles", description="Supabase table name"
    )
    host: str = Field(default="localhost", description="Database host")
    name: str = Field(default="postgres", description="Database name")
    user: str = Field(default="postgres", description="Database user")
    password: SecretStr = Field(
        default=SecretStr("password"), description="Database password"
    )
    port: int = Field(default=6543, description="Database port")
    test_database: str = Field(
        default="substack_test", description="Test database name"
    )


# -----------------------------
# RSS settings
# -----------------------------
class RSSSettings(BaseModel):
    feeds: list[FeedItem] = Field(
        default_factory=list[FeedItem], description="List of RSS feed items"
    )
    default_start_date: str = Field(
        default="2025-09-15", description="Default cutoff date"
    )
    batch_size: int = Field(
        default=5, description="Number of articles to parse and ingest in a batch"
    )


# -----------------------------
# Qdrant settings
# -----------------------------
# BAAI/bge-large-en-v1.5 (1024), BAAI/bge-base-en-v1.5 (HF, 768). BAAI/bge-base-en (Fastembed, 768)
class QdrantSettings(BaseModel):
    url: str = Field(default="", description="Qdrant API URL")
    api_key: str = Field(default="", description="Qdrant API key")
    collection_name: str = Field(
        default="substack_collection", description="Qdrant collection name"
    )
    dense_model_name: str = Field(
        default="BAAI/bge-base-en", description="Dense model name"
    )
    sparse_model_name: str = Field(
        default="Qdrant/bm25", description="Sparse model name"
    )  # prithivida/Splade_PP_en_v1 (larger)
    vector_dim: int = Field(
        default=768,
        description="Vector dimension",  # 768, 1024 with Jina or large HF
    )
    article_batch_size: int = Field(
        default=5, description="Number of articles to parse and ingest in a batch"
    )
    sparse_batch_size: int = Field(default=32, description="Sparse batch size")
    embed_batch_size: int = Field(default=50, description="Dense embedding batch")
    upsert_batch_size: int = Field(
        default=50, description="Batch size for Qdrant upsert"
    )
    max_concurrent: int = Field(
        default=2, description="Maximum number of concurrent tasks"
    )


# -----------------------------
# Re-ranking settings
# -----------------------------
class RerankerSettings(BaseModel):
    enabled: bool = Field(
        default=True,
        description=(
            "Whether to re-score RRF-fused search results with a local cross-encoder "
            "before truncating to the requested limit. Improves ranking quality over "
            "raw RRF fusion at the cost of a small amount of extra CPU time per query."
        ),
    )
    model_name: str = Field(
        default="Xenova/ms-marco-MiniLM-L-6-v2",
        description="Fastembed cross-encoder model used for re-ranking",
    )
    candidate_pool_size: int = Field(
        default=25,
        description=(
            "Number of top RRF-fused results to re-score with the cross-encoder. "
            "Only this many candidates are re-ranked (not the full overfetched set) "
            "to keep per-query latency bounded; the final 'limit' results are drawn "
            "from this pool."
        ),
    )


# -----------------------------
# Text splitting
# -----------------------------
class TextSplitterSettings(BaseModel):
    chunk_size: int = Field(default=4000, description="Size of text chunks")
    chunk_overlap: int = Field(default=200, description="Size of text chunks")
    separators: list[str] = Field(
        default_factory=lambda: [
            "\n---\n",
            "\n\n",
            "\n```\n",
            "\n## ",
            "\n# ",
            "\n**",
            "\n",
            ". ",
            "! ",
            "? ",
            " ",
            "",
        ],
        description="List of separators for text splitting. The order or separators matter",
    )


# -----------------------------
# Jina Settings
# -----------------------------
class JinaSettings(BaseModel):
    api_key: str = Field(default="", description="Jina API key")
    url: str = Field(
        default="https://api.jina.ai/v1/embeddings", description="Jina API URL"
    )
    model: str = Field(
        default="jina-embeddings-v3", description="Jina model name"
    )  # 1024
    timeout_seconds: float = Field(
        default=30.0, description="HTTP timeout in seconds for Jina API calls"
    )


# -----------------------------
# Hugging Face Settings
# -----------------------------
# BAAI/bge-large-en-v1.5 (1024), BAAI/bge-base-en-v1.5 (768)
class HuggingFaceSettings(BaseModel):
    api_key: str = Field(default="", description="Hugging Face API key")
    model: str = Field(
        default="BAAI/bge-base-en-v1.5", description="Hugging Face model name"
    )
    timeout_seconds: float = Field(
        default=30.0, description="HTTP timeout in seconds for Hugging Face API calls"
    )


# -----------------------------
# Openai Settings
# -----------------------------
class OpenAISettings(BaseModel):
    api_key: str | None = Field(default="", description="OpenAI API key")
    # model: str = Field(default="gpt-4o-mini", description="OpenAI model name")
    timeout_seconds: float = Field(
        default=30.0, description="HTTP timeout in seconds for OpenAI API calls"
    )


# -----------------------------
# OpenRouter Settings
# -----------------------------
class OpenRouterSettings(BaseModel):
    api_key: str = Field(default="", description="OpenRouter API key")
    api_url: str = Field(
        default="https://openrouter.ai/api/v1", description="OpenRouter API URL"
    )
    timeout_seconds: float = Field(
        default=30.0, description="HTTP timeout in seconds for OpenRouter API calls"
    )


# -----------------------------
# Opik Observability Settings
# -----------------------------
class OpikObservabilitySettings(BaseModel):
    api_key: str = Field(default="", description="Opik Observability API key")
    project_name: str = Field(
        default="substack-pipeline", description="Opik project name"
    )
    workspace: str = Field(
        default="default",
        description=(
            "Opik/Comet workspace name. The Opik SDK defaults to 'default' if this is "
            "never set — if your actual Comet workspace has a different name, traces "
            "and evaluation scores are silently sent to the wrong workspace and never "
            "show up on your dashboard. Find your real workspace name in the Opik/Comet "
            "dashboard URL."
        ),
    )
    enable_evaluation: bool = Field(
        default=False,
        description=(
            "Whether to run G-Eval quality scoring (faithfulness/coherence/completeness) "
            "on OpenRouter answers. Requires OPENAI__API_KEY (used as the LLM judge) and "
            "adds extra OpenAI API calls per request, run in the background so they don't "
            "add latency to the response."
        ),
    )


# -----------------------------
# API Security Settings
# -----------------------------
class APISecuritySettings(BaseModel):
    api_key: str = Field(
        default="",
        description=(
            "Shared secret required (via the X-API-Key header) to call /search/* "
            "endpoints. Auth fails closed: if this is empty, every request to a "
            "protected route is rejected rather than silently allowed through."
        ),
    )
    rate_limit_max_requests: int = Field(
        default=30,
        description="Max requests allowed per client IP within rate_limit_window_seconds",
    )
    rate_limit_window_seconds: int = Field(
        default=60, description="Rate limit window size, in seconds"
    )


# -----------------------------
# YAML loader
# -----------------------------
def load_yaml_feeds(path: str) -> list[FeedItem]:
    """
    Load RSS feed items from a YAML file.
    If the file does not exist or is empty, returns an empty list.

    Args:
        path (str): Path to the YAML file.

    Returns:
        list[FeedItem]: List of FeedItem instances loaded from the file.
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    feed_list = data.get("feeds", [])
    return [FeedItem(**feed) for feed in feed_list]


# -----------------------------
# Main Settings
# -----------------------------
class Settings(BaseSettings):
    supabase_db: SupabaseDBSettings = Field(default_factory=SupabaseDBSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    rss: RSSSettings = Field(default_factory=RSSSettings)
    text_splitter: TextSplitterSettings = Field(default_factory=TextSplitterSettings)

    jina: JinaSettings = Field(default_factory=JinaSettings)
    hugging_face: HuggingFaceSettings = Field(default_factory=HuggingFaceSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    opik: OpikObservabilitySettings = Field(default_factory=OpikObservabilitySettings)
    api_security: APISecuritySettings = Field(default_factory=APISecuritySettings)

    rss_config_yaml_path: str = "src/configs/feeds_rss.yaml"

    # Pydantic v2 model config
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=[".env"],
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
        case_sensitive=False,
        frozen=True,
    )

    @model_validator(mode="after")
    def load_yaml_rss_feeds(self) -> "Settings":
        """
        Load RSS feeds from a YAML file after model initialization.
        If the file does not exist or is empty, the feeds list remains unchanged.

        Args:
            self (Settings): The settings instance.

        Returns:
            Settings: The updated settings instance.
        """
        yaml_feeds = load_yaml_feeds(self.rss_config_yaml_path)
        if yaml_feeds:
            self.rss.feeds = yaml_feeds
        return self


# -----------------------------
# Instantiate settings
# -----------------------------
settings = Settings()
