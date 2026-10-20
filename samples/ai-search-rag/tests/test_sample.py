import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.search.documents.indexes.models import SearchIndexerStatus

from app import config, create, delete, indexing, invoke
from app.config import EMBEDDING_DIMENSIONS, Settings

SETTINGS = Settings(
    search_endpoint="https://example.search.windows.net",
    storage_account_url="https://example.blob.core.windows.net",
    storage_resource_id=(
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/example"
    ),
    openai_endpoint="https://example.openai.azure.com",
    embedding_deployment="embeddings",
    chat_deployment="chat",
)
START = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", MagicMock())
    for name, value in {
        "AZURE_SEARCH_ENDPOINT": SETTINGS.search_endpoint,
        "AZURE_STORAGE_ACCOUNT_URL": SETTINGS.storage_account_url,
        "AZURE_STORAGE_RESOURCE_ID": SETTINGS.storage_resource_id,
        "AZURE_OPENAI_ENDPOINT": SETTINGS.openai_endpoint,
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": SETTINGS.embedding_deployment,
        "AZURE_OPENAI_CHAT_DEPLOYMENT": SETTINGS.chat_deployment,
        "AZURE_SEARCH_RESOURCE_PREFIX": SETTINGS.prefix,
    }.items():
        monkeypatch.setenv(name, value)


def execution(
    status: str = "success", start: datetime = START, failed: int = 0
) -> SearchIndexerStatus:
    return SearchIndexerStatus.deserialize({
        "status": "running",
        "lastResult": {
            "status": status,
            "startTime": start.isoformat(),
            "itemsProcessed": 3,
            "itemsFailed": failed,
            "errorMessage": "embedding request failed" if "Failure" in status else None,
        },
    })


def context_client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    return client


def patch_clients(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> dict[str, MagicMock]:
    clients = {}
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    for name in (
        "DefaultAzureCredential", "BlobServiceClient", "SearchIndexClient",
        "SearchIndexerClient", "SearchClient", "OpenAI",
    ):
        if hasattr(module, name):
            clients[name] = context_client()
            monkeypatch.setattr(module, name, MagicMock(return_value=clients[name]))
    return clients


def test_settings_load_sample_env_and_preserve_exports(environment: None) -> None:
    assert config.get_settings() == SETTINGS
    config.load_dotenv.assert_called_once_with(config.SAMPLE_DIR / ".env", override=False)


@pytest.mark.parametrize(
    "name",
    [
        "AZURE_SEARCH_ENDPOINT", "AZURE_STORAGE_ACCOUNT_URL", "AZURE_STORAGE_RESOURCE_ID",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    ],
)
def test_settings_require_ingestion_values(
    environment: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, " ")
    with pytest.raises(RuntimeError, match=name):
        config.get_settings()


def test_settings_allow_ingestion_without_chat(
    environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
    assert config.get_settings().chat_deployment == ""


@pytest.mark.parametrize("prefix", ["x", "Uppercase", "bad--name", "-bad", "bad-", "a" * 41])
def test_settings_reject_unsafe_names(
    environment: None, monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    monkeypatch.setenv("AZURE_SEARCH_RESOURCE_PREFIX", prefix)
    with pytest.raises(RuntimeError, match="AZURE_SEARCH_RESOURCE_PREFIX"):
        config.get_settings()


@pytest.mark.parametrize(
    "value", ["http://example.com", "https://example.com/api/projects/demo", "https://user@example.com"]
)
def test_settings_require_resource_endpoints(
    environment: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", value)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_ENDPOINT"):
        config.get_settings()


def test_settings_reject_connection_string_injection(
    environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_RESOURCE_ID", SETTINGS.storage_resource_id + ";AccountKey=x")
    with pytest.raises(RuntimeError, match="AZURE_STORAGE_RESOURCE_ID"):
        config.get_settings()


def test_shared_guidelines_are_not_copied() -> None:
    paths = indexing.guideline_paths()
    assert [path.name for path in paths] == [
        "governance-and-cost.md", "platform-security.md", "reliability-and-recovery.md"
    ]
    assert all(path.parent == config.GUIDELINES_DIR for path in paths)
    assert "enterprise-architect-prompt-agent" in config.GUIDELINES_DIR.parts


def test_missing_guidelines_fail_before_azure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="No Markdown"):
        indexing.guideline_paths(tmp_path)


def test_upload_overwrites_only_shared_markdown() -> None:
    storage = MagicMock()
    container = storage.get_container_client.return_value
    container.create_container.side_effect = ResourceExistsError("already exists")
    paths = indexing.guideline_paths()
    indexing.upload_guidelines(storage, SETTINGS, paths)
    storage.get_container_client.assert_called_once_with(SETTINGS.container_name)
    assert container.upload_blob.call_count == 3
    for call, path in zip(container.upload_blob.call_args_list, paths):
        assert call.kwargs["name"] == path.name
        assert call.kwargs["overwrite"] is True
        assert call.kwargs["content_settings"].content_type.startswith("text/markdown")
        assert call.kwargs["data"].closed


def test_upload_does_not_hide_storage_errors() -> None:
    storage = MagicMock()
    storage.get_container_client.return_value.create_container.side_effect = HttpResponseError("denied")
    with pytest.raises(HttpResponseError, match="denied"):
        indexing.upload_guidelines(storage, SETTINGS, indexing.guideline_paths())


def test_pipeline_serializes_real_sdk_models() -> None:
    index = indexing.build_index(SETTINGS).serialize()
    fields = {field["name"]: field for field in index["fields"]}
    assert fields["chunk_id"]["key"] is True
    assert fields["chunk_id"]["analyzer"] == "keyword"
    assert fields["parent_id"]["filterable"] is True
    assert fields["vector"]["dimensions"] == EMBEDDING_DIMENSIONS
    assert fields["vector"]["retrievable"] is False
    profile = index["vectorSearch"]["profiles"][0]
    vectorizer = index["vectorSearch"]["vectorizers"][0]
    assert profile["vectorizer"] == vectorizer["name"]
    assert fields["vector"]["vectorSearchProfile"] == profile["name"]
    parameters = vectorizer["azureOpenAIParameters"]
    assert parameters["modelName"] == "text-embedding-3-small"
    assert parameters["deploymentId"] == SETTINGS.embedding_deployment
    assert not parameters.get("apiKey")
    assert not parameters.get("authIdentity")

    source = indexing.build_data_source(SETTINGS).serialize()
    assert source["type"] == "azureblob"
    assert source["credentials"]["connectionString"] == f"ResourceId={SETTINGS.storage_resource_id};"
    assert source["container"]["name"] == SETTINGS.container_name

    skills = indexing.build_skillset(SETTINGS).serialize()
    split, embedding = skills["skills"]
    assert split["maximumPageLength"] == 2000
    assert split["pageOverlapLength"] == 500
    assert embedding["dimensions"] == fields["vector"]["dimensions"]
    assert embedding["deploymentId"] == parameters["deploymentId"]
    assert embedding["modelName"] == parameters["modelName"]
    assert embedding["resourceUri"] == parameters["resourceUri"]
    assert embedding["context"] == "/document/pages/*"
    assert embedding["inputs"] == [{"name": "text", "source": "/document/pages/*"}]
    assert not embedding.get("apiKey")
    projection = skills["indexProjections"]
    assert projection["parameters"]["projectionMode"] == "skipIndexingParentDocuments"
    selector = projection["selectors"][0]
    assert selector["targetIndexName"] == SETTINGS.index_name
    assert selector["parentKeyFieldName"] == "parent_id"
    assert {entry["name"]: entry["source"] for entry in selector["mappings"]} == {
        "chunk": "/document/pages/*",
        "vector": "/document/pages/*/vector",
        "title": "/document/metadata_storage_name",
        "source": "/document/metadata_storage_path",
    }

    indexer = indexing.build_indexer(SETTINGS).serialize()
    assert indexer["dataSourceName"] == source["name"]
    assert indexer["targetIndexName"] == index["name"]
    assert indexer["skillsetName"] == skills["name"]
    assert indexer["parameters"]["maxFailedItems"] == 0
    assert indexer["parameters"]["maxFailedItemsPerBatch"] == 0
    assert indexer["parameters"]["configuration"] == {
        "parsingMode": "text",
        "dataToExtract": "contentAndMetadata",
        "indexedFileNameExtensions": ".md",
    }
    assert not indexer.get("schedule")


def test_wait_ignores_previous_completed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_indexer_status.side_effect = [
        execution(), SearchIndexerStatus.deserialize({"status": "running"}),
        execution("inProgress", START + timedelta(seconds=1)),
        execution("success", START + timedelta(seconds=1)),
    ]
    monkeypatch.setattr(indexing.time, "sleep", MagicMock())
    indexing.wait_for_indexer(client, "indexer", previous_start=START)
    assert client.get_indexer_status.call_count == 4


@pytest.mark.parametrize("state", ["transientFailure", "persistentFailure", "reset"])
def test_wait_surfaces_execution_failure(state: str) -> None:
    client = MagicMock()
    client.get_indexer_status.return_value = execution(state)
    with pytest.raises(RuntimeError, match=state):
        indexing.wait_for_indexer(client, "indexer")


def test_wait_surfaces_failed_items() -> None:
    client = MagicMock()
    client.get_indexer_status.return_value = execution(failed=1)
    with pytest.raises(RuntimeError, match="1 items"):
        indexing.wait_for_indexer(client, "indexer")


def test_wait_surfaces_service_error() -> None:
    client = MagicMock()
    client.get_indexer_status.return_value = SearchIndexerStatus.deserialize({"status": "error"})
    with pytest.raises(RuntimeError, match="error state"):
        indexing.wait_for_indexer(client, "indexer")


def test_wait_reports_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock()
    status = execution()
    status.last_result.warnings = ["truncated document"]
    client.get_indexer_status.return_value = status
    indexing.wait_for_indexer(client, "indexer")
    assert "truncated document" in capsys.readouterr().out


def test_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_indexer_status.return_value = execution("inProgress")
    monkeypatch.setattr(indexing.time, "monotonic", MagicMock(side_effect=[0, 0, 1, 2]))
    monkeypatch.setattr(indexing.time, "sleep", MagicMock())
    with pytest.raises(TimeoutError, match="may still be active"):
        indexing.wait_for_indexer(client, "indexer", timeout=1)
    client.get_indexer_status.assert_called_once()


@pytest.mark.parametrize("existing", [False, True])
def test_create_uploads_provisions_and_triggers_correctly(
    monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    clients = patch_clients(monkeypatch, create)
    indexers = clients["SearchIndexerClient"]
    if existing:
        indexers.get_indexer_status.return_value = execution()
    else:
        indexers.get_indexer.side_effect = ResourceNotFoundError("new")
    upload, wait = MagicMock(), MagicMock()
    monkeypatch.setattr(create, "upload_guidelines", upload)
    monkeypatch.setattr(create, "wait_for_indexer", wait)
    monkeypatch.setattr("sys.argv", ["create", "--timeout", "60"])

    create.main()

    upload.assert_called_once_with(clients["BlobServiceClient"], SETTINGS, indexing.guideline_paths())
    assert clients["SearchIndexClient"].create_or_update_index.call_args.args[0].name == SETTINGS.index_name
    assert indexers.create_or_update_data_source_connection.call_args.args[0].name == SETTINGS.data_source_name
    assert indexers.create_or_update_skillset.call_args.args[0].name == SETTINGS.skillset_name
    assert indexers.create_or_update_indexer.call_args.args[0].name == SETTINGS.indexer_name
    assert indexers.run_indexer.call_count == int(existing)
    wait.assert_called_once_with(
        indexers, SETTINGS.indexer_name, previous_start=START if existing else None, timeout=60
    )


def test_create_rejects_concurrent_run_before_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, create)
    clients["SearchIndexerClient"].get_indexer_status.return_value = execution("inProgress")
    monkeypatch.setattr("sys.argv", ["create"])
    with pytest.raises(RuntimeError, match="already running"):
        create.main()
    clients["BlobServiceClient"].get_container_client.assert_not_called()
    clients["SearchIndexClient"].create_or_update_index.assert_not_called()


def test_create_does_not_report_ready_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, create)
    clients["SearchIndexerClient"].get_indexer.side_effect = ResourceNotFoundError("new")
    clients["SearchIndexClient"].create_or_update_index.side_effect = HttpResponseError("bad index")
    monkeypatch.setattr(create, "upload_guidelines", MagicMock())
    wait = MagicMock()
    monkeypatch.setattr(create, "wait_for_indexer", wait)
    monkeypatch.setattr("sys.argv", ["create"])
    with pytest.raises(HttpResponseError, match="bad index"):
        create.main()
    wait.assert_not_called()


def test_hybrid_retrieval_returns_citation_metadata() -> None:
    client = MagicMock()
    client.search.return_value = [
        {"chunk_id": "chunk-1", "title": "reliability.md", "source": "https://blob/source", "chunk": "REL-003"}
    ]
    chunks = invoke.retrieve(client, "Tier 1 recovery?", 3)
    assert chunks == [invoke.Chunk("chunk-1", "reliability.md", "https://blob/source", "REL-003")]
    args = client.search.call_args.kwargs
    assert args["search_text"] == "Tier 1 recovery?"
    assert args["top"] == 3
    assert args["select"] == ["chunk_id", "title", "source", "chunk"]
    assert args["vector_queries"][0].serialize() == {
        "kind": "text", "text": "Tier 1 recovery?", "k": 50, "fields": "vector"
    }


def test_generation_uses_numbered_retrieved_context() -> None:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text="Quarterly [1]."
    )
    chunk = invoke.Chunk("c1", "reliability.md", "https://blob/source", "REL-003: quarterly")
    assert invoke.generate(client, "chat", "Restore tests?", [chunk]) == "Quarterly [1]."
    args = client.responses.create.call_args.kwargs
    assert args["model"] == "chat"
    assert args["instructions"] == invoke.INSTRUCTIONS
    assert json.loads(args["input"]) == {
        "question": "Restore tests?",
        "excerpts": [{"citation": "[1]", "id": "c1", "title": "reliability.md",
                      "source": "https://blob/source", "text": "REL-003: quarterly"}],
    }


@pytest.mark.parametrize("status,text", [("incomplete", "partial"), ("completed", " ")])
def test_generation_rejects_incomplete_or_empty_answers(status: str, text: str) -> None:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(status=status, output_text=text)
    with pytest.raises(RuntimeError, match="completed answer"):
        invoke.generate(client, "chat", "question", [])


def test_search_only_needs_no_chat_and_skips_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clients = patch_clients(monkeypatch, invoke)
    monkeypatch.setattr(invoke, "get_settings", lambda: replace(SETTINGS, chat_deployment=""))
    clients["SearchClient"].search.return_value = [
        {"chunk_id": "c", "title": "policy.md", "source": "https://blob", "chunk": "Policy"}
    ]
    monkeypatch.setattr("sys.argv", ["invoke", "--search-only", "question"])
    invoke.main()
    assert json.loads(capsys.readouterr().out)[0]["text"] == "Policy"
    invoke.OpenAI.assert_not_called()


def test_invoke_empty_index_does_not_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, invoke)
    clients["SearchClient"].search.return_value = []
    monkeypatch.setattr("sys.argv", ["invoke", "question"])
    with pytest.raises(RuntimeError, match="No guideline chunks"):
        invoke.main()
    invoke.OpenAI.assert_not_called()


def test_invoke_prints_answer_and_sources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clients = patch_clients(monkeypatch, invoke)
    clients["SearchClient"].search.return_value = [
        {"chunk_id": "c", "title": "policy.md", "source": "https://blob", "chunk": "Policy"}
    ]
    clients["OpenAI"].responses.create.return_value = SimpleNamespace(
        status="completed", output_text="Answer [1]"
    )
    token_provider = MagicMock()
    monkeypatch.setattr(invoke, "get_bearer_token_provider", MagicMock(return_value=token_provider))
    monkeypatch.setattr("sys.argv", ["invoke", "question"])
    invoke.main()
    invoke.OpenAI.assert_called_once_with(
        base_url=f"{SETTINGS.openai_endpoint}/openai/v1/", api_key=token_provider
    )
    output = capsys.readouterr().out
    assert "Answer [1]" in output
    assert "[1] policy.md (c) - https://blob" in output


@pytest.mark.parametrize("args", [["--top", "0", "q"], ["--top", "21", "q"], [" "]])
def test_invoke_validates_input(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["invoke", *args])
    with pytest.raises(SystemExit, match="2"):
        invoke.main()


def test_invoke_requires_chat_for_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_clients(monkeypatch, invoke)
    monkeypatch.setattr(invoke, "get_settings", lambda: replace(SETTINGS, chat_deployment=""))
    monkeypatch.setattr("sys.argv", ["invoke", "question"])
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_CHAT_DEPLOYMENT"):
        invoke.main()


@pytest.mark.parametrize("blobs", [False, True])
def test_delete_only_named_resources(monkeypatch: pytest.MonkeyPatch, blobs: bool) -> None:
    clients = patch_clients(monkeypatch, delete)
    monkeypatch.setattr("sys.argv", ["delete", *(["--delete-blobs"] if blobs else [])])
    delete.main()
    clients["SearchIndexerClient"].delete_indexer.assert_called_once_with(SETTINGS.indexer_name)
    clients["SearchIndexerClient"].delete_skillset.assert_called_once_with(SETTINGS.skillset_name)
    clients["SearchIndexerClient"].delete_data_source_connection.assert_called_once_with(SETTINGS.data_source_name)
    clients["SearchIndexClient"].delete_index.assert_called_once_with(SETTINGS.index_name)
    if blobs:
        clients["BlobServiceClient"].delete_container.assert_called_once_with(SETTINGS.container_name)
    else:
        delete.BlobServiceClient.assert_not_called()


def test_delete_reports_already_absent_resources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clients = patch_clients(monkeypatch, delete)
    clients["SearchIndexerClient"].delete_indexer.side_effect = ResourceNotFoundError("missing")
    monkeypatch.setattr("sys.argv", ["delete"])
    delete.main()
    assert f"Already absent: {SETTINGS.indexer_name}" in capsys.readouterr().out
    clients["SearchIndexClient"].delete_index.assert_called_once()
