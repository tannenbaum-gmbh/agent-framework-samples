"""Delete this sample's search objects; optionally delete its dedicated blob container."""

from __future__ import annotations

import argparse

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.storage.blob import BlobServiceClient

from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete-blobs",
        action="store_true",
        help="Also delete the entire dedicated sample container, including all its blobs.",
    )
    args = parser.parse_args()
    settings = get_settings()
    with (
        DefaultAzureCredential() as credential,
        SearchIndexClient(settings.search_endpoint, credential) as indexes,
        SearchIndexerClient(settings.search_endpoint, credential) as indexers,
    ):
        for delete, name in (
            (indexers.delete_indexer, settings.indexer_name),
            (indexers.delete_skillset, settings.skillset_name),
            (indexers.delete_data_source_connection, settings.data_source_name),
            (indexes.delete_index, settings.index_name),
        ):
            try:
                delete(name)
            except ResourceNotFoundError:
                print(f"Already absent: {name}")
            else:
                print(f"Deleted {name}")
        if args.delete_blobs:
            with BlobServiceClient(settings.storage_account_url, credential=credential) as storage:
                try:
                    storage.delete_container(settings.container_name)
                except ResourceNotFoundError:
                    print(f"Already absent: {settings.container_name}")
                else:
                    print(f"Deleted container {settings.container_name}")


if __name__ == "__main__":
    main()
