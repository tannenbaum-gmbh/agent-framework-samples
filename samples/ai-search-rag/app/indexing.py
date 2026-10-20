"""Build the classic data source -> skillset -> projected chunk index pipeline."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.search.documents.indexes import SearchIndexerClient
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    IndexingParameters,
    IndexingParametersConfiguration,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient, ContentSettings

from .config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, GUIDELINES_DIR, Settings


def guideline_paths(directory: Path = GUIDELINES_DIR) -> list[Path]:
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise RuntimeError(f"No Markdown guidelines found in {directory}.")
    return paths


def upload_guidelines(
    storage: BlobServiceClient, settings: Settings, paths: list[Path]
) -> None:
    container = storage.get_container_client(settings.container_name)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    for path in paths:
        with path.open("rb") as content:
            container.upload_blob(
                name=path.name,
                data=content,
                overwrite=True,
                content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
            )
        print(f"Uploaded {path.name}")


def build_index(settings: Settings) -> SearchIndex:
    return SearchIndex(
        name=settings.index_name,
        fields=[
            SearchField(
                name="chunk_id",
                type=SearchFieldDataType.String,
                key=True,
                searchable=True,
                analyzer_name="keyword",
            ),
            SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="chunk", type=SearchFieldDataType.String, searchable=True),
            SimpleField(name="title", type=SearchFieldDataType.String),
            SimpleField(name="source", type=SearchFieldDataType.String),
            SearchField(
                name="vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                hidden=True,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="guidelines-vector-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="guidelines-hnsw")],
            profiles=[
                VectorSearchProfile(
                    name="guidelines-vector-profile",
                    algorithm_configuration_name="guidelines-hnsw",
                    vectorizer_name="guidelines-vectorizer",
                )
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="guidelines-vectorizer",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=settings.openai_endpoint,
                        deployment_name=settings.embedding_deployment,
                        model_name=EMBEDDING_MODEL,
                    ),
                )
            ],
        ),
    )


def build_data_source(settings: Settings) -> SearchIndexerDataSourceConnection:
    return SearchIndexerDataSourceConnection(
        name=settings.data_source_name,
        type="azureblob",
        connection_string=f"ResourceId={settings.storage_resource_id};",
        container=SearchIndexerDataContainer(name=settings.container_name),
    )


def build_skillset(settings: Settings) -> SearchIndexerSkillset:
    return SearchIndexerSkillset(
        name=settings.skillset_name,
        skills=[
            SplitSkill(
                name="split-guidelines",
                context="/document",
                text_split_mode="pages",
                maximum_page_length=2000,
                page_overlap_length=500,
                inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
            ),
            AzureOpenAIEmbeddingSkill(
                name="embed-chunks",
                context="/document/pages/*",
                resource_url=settings.openai_endpoint,
                deployment_name=settings.embedding_deployment,
                model_name=EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
                outputs=[OutputFieldMappingEntry(name="embedding", target_name="vector")],
            ),
        ],
        index_projection=SearchIndexerIndexProjection(
            selectors=[
                SearchIndexerIndexProjectionSelector(
                    target_index_name=settings.index_name,
                    parent_key_field_name="parent_id",
                    source_context="/document/pages/*",
                    mappings=[
                        InputFieldMappingEntry(name="chunk", source="/document/pages/*"),
                        InputFieldMappingEntry(name="vector", source="/document/pages/*/vector"),
                        InputFieldMappingEntry(name="title", source="/document/metadata_storage_name"),
                        InputFieldMappingEntry(name="source", source="/document/metadata_storage_path"),
                    ],
                )
            ],
            parameters=SearchIndexerIndexProjectionsParameters(
                projection_mode="skipIndexingParentDocuments"
            ),
        ),
    )


def build_indexer(settings: Settings) -> SearchIndexer:
    return SearchIndexer(
        name=settings.indexer_name,
        data_source_name=settings.data_source_name,
        target_index_name=settings.index_name,
        skillset_name=settings.skillset_name,
        parameters=IndexingParameters(
            max_failed_items=0,
            max_failed_items_per_batch=0,
            # Deserialization omits constructor defaults, including SQL-only queryTimeout.
            configuration=IndexingParametersConfiguration.deserialize({
                "parsingMode": "text",
                "dataToExtract": "contentAndMetadata",
                "indexedFileNameExtensions": ".md",
            }),
        ),
    )


def wait_for_indexer(
    client: SearchIndexerClient,
    name: str,
    *,
    previous_start: datetime | None = None,
    timeout: float = 600,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get_indexer_status(name)
        if status.status == "error":
            raise RuntimeError(f"Indexer {name} is in an error state: {status}")
        result = status.last_result
        if result is not None and result.start_time != previous_start:
            if result.status == "success":
                if result.failed_item_count:
                    raise RuntimeError(f"Indexer failed for {result.failed_item_count} items.")
                for warning in result.warnings or []:
                    print(f"Indexer warning: {warning}")
                print(f"Indexer succeeded: {result.item_count} items processed.")
                return
            if result.status in ("transientFailure", "persistentFailure", "reset"):
                raise RuntimeError(
                    f"Indexer {name} failed ({result.status}): "
                    f"{result.error_message}; errors={result.errors}"
                )
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise TimeoutError(
        f"Timed out waiting for indexer {name}. The Azure run may still be active; "
        "inspect its execution history before rerunning."
    )
