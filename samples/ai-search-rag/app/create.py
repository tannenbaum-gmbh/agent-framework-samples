"""Upload shared guidelines, configure classic indexing, and wait for ingestion."""

from __future__ import annotations

import argparse

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.storage.blob import BlobServiceClient

from .config import get_settings
from .indexing import (
    build_data_source,
    build_index,
    build_indexer,
    build_skillset,
    guideline_paths,
    upload_guidelines,
    wait_for_indexer,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=600, help="Indexer wait in seconds.")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")
    settings = get_settings()
    paths = guideline_paths()

    with (
        DefaultAzureCredential() as credential,
        BlobServiceClient(settings.storage_account_url, credential=credential) as storage,
        SearchIndexClient(settings.search_endpoint, credential) as indexes,
        SearchIndexerClient(settings.search_endpoint, credential) as indexers,
    ):
        try:
            indexers.get_indexer(settings.indexer_name)
        except ResourceNotFoundError:
            existing = False
            previous_start = None
        else:
            existing = True
            status = indexers.get_indexer_status(settings.indexer_name)
            if status.last_result and status.last_result.status == "inProgress":
                raise RuntimeError("Indexer is already running. Wait before updating the pipeline.")
            previous_start = status.last_result.start_time if status.last_result else None

        upload_guidelines(storage, settings, paths)
        indexes.create_or_update_index(build_index(settings))
        indexers.create_or_update_data_source_connection(build_data_source(settings))
        indexers.create_or_update_skillset(build_skillset(settings))
        indexers.create_or_update_indexer(build_indexer(settings))
        if existing:
            indexers.run_indexer(settings.indexer_name)
        # A newly created indexer runs automatically; do not submit a competing run.
        wait_for_indexer(
            indexers,
            settings.indexer_name,
            previous_start=previous_start,
            timeout=args.timeout,
        )
        print(f"Ready: {settings.index_name} ({len(paths)} source documents).")


if __name__ == "__main__":
    main()
