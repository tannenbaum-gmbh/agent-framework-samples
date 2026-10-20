import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from azure.core import MatchConditions
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchField,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import KnowledgeBaseRetrievalRequest

from app import agent, config, connection, create, delete, invoke, knowledge
from app.config import SEARCH_API_VERSION, SEMANTIC_CONFIGURATION, Settings

SETTINGS = Settings(
    search_endpoint="https://example.search.windows.net",
    index_name="classic-rag-chunks",
    project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    openai_endpoint="https://example.openai.azure.com",
    model_deployment="architecture-model",
    project_resource_id=(
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.CognitiveServices/accounts/example/projects/demo"
    ),
)


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", MagicMock())
    for name in (
        "AZURE_SEARCH_INDEX_NAME", "AZURE_SEARCH_RESOURCE_PREFIX",
        "FOUNDRY_IQ_RESOURCE_PREFIX", "FOUNDRY_MODEL_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "AZURE_SEARCH_ENDPOINT": SETTINGS.search_endpoint,
        "FOUNDRY_PROJECT_ENDPOINT": SETTINGS.project_endpoint,
        "AZURE_OPENAI_ENDPOINT": SETTINGS.openai_endpoint,
        "FOUNDRY_MODEL_DEPLOYMENT": SETTINGS.model_deployment,
        "FOUNDRY_PROJECT_RESOURCE_ID": SETTINGS.project_resource_id,
    }.items():
        monkeypatch.setenv(name, value)


def test_settings_reuse_classic_index_without_storage_or_embedding(environment: None) -> None:
    assert config.get_settings() == SETTINGS
    config.load_dotenv.assert_called_once_with(config.SAMPLE_DIR / ".env", override=False)


def test_index_overrides(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SEARCH_RESOURCE_PREFIX", "alice-rag")
    assert config.get_settings().index_name == "alice-rag-chunks"
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "shared-chunks")
    assert config.get_settings().index_name == "shared-chunks"
    monkeypatch.setenv("FOUNDRY_IQ_RESOURCE_PREFIX", "alice-iq")
    assert config.get_settings().knowledge_base_name == "alice-iq-kb"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AZURE_SEARCH_ENDPOINT", ""),
        ("FOUNDRY_MODEL_DEPLOYMENT", " "),
        ("AZURE_SEARCH_ENDPOINT", "http://example.com"),
        ("AZURE_OPENAI_ENDPOINT", "https://example.com/openai/v1"),
        ("AZURE_SEARCH_ENDPOINT", "https://user@example.com"),
        ("FOUNDRY_PROJECT_ENDPOINT", "https://example.com"),
        ("FOUNDRY_PROJECT_ENDPOINT", "https://example.com/api/projects/demo?key=x"),
        ("FOUNDRY_PROJECT_RESOURCE_ID", "/subscriptions/sub?injected=value"),
        ("FOUNDRY_IQ_RESOURCE_PREFIX", "bad--name"),
        ("FOUNDRY_IQ_RESOURCE_PREFIX", "a" * 41),
        ("AZURE_SEARCH_INDEX_NAME", "Uppercase"),
    ],
)
def test_invalid_configuration_fails(
    environment: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        config.get_settings()


def test_resource_id_only_needed_for_connection(
    environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FOUNDRY_PROJECT_RESOURCE_ID")
    settings = config.get_settings()
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_RESOURCE_ID"):
        settings.require_project_resource_id()


def classic_index() -> SearchIndex:
    return SearchIndex(
        name=SETTINGS.index_name,
        e_tag='"revision-1"',
        fields=[
            SearchField(name="chunk_id", type="Edm.String", key=True),
            SearchField(name="chunk", type="Edm.String", searchable=True),
            SearchField(name="title", type="Edm.String"),
            SearchField(name="source", type="Edm.String"),
            SearchField(
                name="vector", type="Collection(Edm.Single)", searchable=True,
                hidden=True, vector_search_dimensions=1536,
                vector_search_profile_name="existing-profile",
            ),
        ],
        vector_search={
            "algorithms": [{"name": "existing-hnsw", "kind": "hnsw"}],
            "profiles": [{"name": "existing-profile", "algorithm": "existing-hnsw"}],
        },
    )


def test_semantic_update_is_additive_and_conditional() -> None:
    client = MagicMock()
    index = classic_index()
    index.semantic_search = SemanticSearch(
        default_configuration_name="existing",
        configurations=[
            SemanticConfiguration(
                name="existing",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="chunk")]
                ),
            )
        ],
    )
    original = index.as_dict()
    client.get_index.return_value = index
    knowledge.ensure_semantic_configuration(client, SETTINGS)
    client.create_or_update_index.assert_called_once_with(
        index, match_condition=MatchConditions.IfNotModified
    )
    updated = index.as_dict()
    assert updated["fields"] == original["fields"]
    assert updated["vectorSearch"] == original["vectorSearch"]
    assert updated["@odata.etag"] == original["@odata.etag"]
    assert updated["semantic"]["defaultConfiguration"] == "existing"
    assert [item["name"] for item in updated["semantic"]["configurations"]] == [
        "existing", SEMANTIC_CONFIGURATION
    ]
    assert updated["semantic"]["configurations"][1]["prioritizedFields"] == {
        "prioritizedContentFields": [{"fieldName": "chunk"}]
    }
    knowledge.ensure_semantic_configuration(client, SETTINGS)
    assert client.create_or_update_index.call_count == 1


@pytest.mark.parametrize("invalid", ["missing", "hidden", "not-searchable", "not-key", "wrong-type"])
def test_incompatible_index_is_not_overwritten(invalid: str) -> None:
    index = classic_index()
    if invalid == "missing":
        index.fields = [field for field in index.fields if field.name != "chunk"]
    elif invalid == "hidden":
        index.fields[1].hidden = True
    elif invalid == "not-searchable":
        index.fields[1].searchable = False
    elif invalid == "not-key":
        index.fields[0].key = False
    else:
        index.fields[1].type = "Edm.Int32"
    client = MagicMock()
    client.get_index.return_value = index
    with pytest.raises(RuntimeError, match="index|Index"):
        knowledge.ensure_semantic_configuration(client, SETTINGS)
    client.create_or_update_index.assert_not_called()


def test_conflicting_semantic_configuration_is_not_replaced() -> None:
    index = classic_index()
    index.semantic_search = SemanticSearch(configurations=[
        SemanticConfiguration(
            name=SEMANTIC_CONFIGURATION,
            prioritized_fields=SemanticPrioritizedFields(
                content_fields=[SemanticField(field_name="other")]
            ),
        )
    ])
    client = MagicMock()
    client.get_index.return_value = index
    with pytest.raises(RuntimeError, match="does not prioritize"):
        knowledge.ensure_semantic_configuration(client, SETTINGS)
    client.create_or_update_index.assert_not_called()


def test_sources_serialize_all_three_real_sdk_contracts() -> None:
    internal, mslearn, web = [source.as_dict() for source in knowledge.build_sources(SETTINGS)]
    assert internal["kind"] == "searchIndex"
    parameters = internal["searchIndexParameters"]
    assert parameters["searchIndexName"] == SETTINGS.index_name
    assert parameters["semanticConfigurationName"] == SEMANTIC_CONFIGURATION
    assert parameters["searchFields"] == [{"name": "chunk"}]
    assert parameters["sourceDataFields"] == [
        {"name": name} for name in ("chunk_id", "chunk", "title", "source")
    ]
    assert mslearn["kind"] == "mcpServer"
    assert mslearn["mcpServerParameters"] == {
        "serverURL": knowledge.MSLEARN_MCP_URL,
        "tools": [{
            "name": "microsoft_docs_search",
            "outputParsing": {"kind": "auto"},
            "resultsProcessing": "none",
            "maxOutputTokens": 2000,
        }],
    }
    assert web["kind"] == "web"
    assert web["webParameters"] == {}


def test_knowledge_base_owns_federation_and_agent_uses_only_it() -> None:
    kb = knowledge.build_knowledge_base(SETTINGS).as_dict()
    assert kb["knowledgeSources"] == [{"name": name} for name in SETTINGS.source_names]
    assert kb["outputMode"] == "extractiveData"
    assert kb["retrievalReasoningEffort"] == {"kind": "low"}
    assert kb["models"][0]["azureOpenAIParameters"] == {
        "resourceUri": SETTINGS.openai_endpoint,
        "deploymentId": SETTINGS.model_deployment,
        "modelName": SETTINGS.model_name,
    }
    assert "apiKey" not in json.dumps(kb)
    definition = agent.create_definition(SETTINGS).as_dict()
    assert definition["model"] == SETTINGS.model_deployment
    assert definition["tools"] == [{
        "type": "mcp",
        "server_label": "FoundryIQ",
        "server_url": SETTINGS.mcp_endpoint,
        "project_connection_id": SETTINGS.connection_name,
        "allowed_tools": ["knowledge_base_retrieve"],
        "require_approval": "never",
    }]
    assert "Internal policy wins" in definition["instructions"]
    assert "external MCP and web queries" in kb["retrievalInstructions"]


def context_client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    return client


def patch_clients(monkeypatch: pytest.MonkeyPatch, module: object) -> dict[str, MagicMock]:
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr("sys.argv", ["sample"])
    clients = {}
    for name in (
        "DefaultAzureCredential", "AIProjectClient",
        "SearchIndexClient", "KnowledgeBaseRetrievalClient",
    ):
        if hasattr(module, name):
            client = context_client()
            clients[name] = client
            monkeypatch.setattr(module, name, MagicMock(return_value=client))
    return clients


def test_create_order_and_no_reingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, create)
    indexes = clients["SearchIndexClient"]
    indexes.get_index.return_value = classic_index()
    manage = MagicMock()
    monkeypatch.setattr(create, "manage_connection", manage)
    create.main()
    assert indexes.create_or_update_knowledge_source.call_count == 3
    indexes.create_or_update_knowledge_base.assert_called_once()
    manage.assert_called_once_with(clients["DefaultAzureCredential"], SETTINGS)
    clients["AIProjectClient"].agents.create_version.assert_called_once()
    indexes.delete_index.assert_not_called()
    indexes.create_or_update_indexer.assert_not_called()
    create.SearchIndexClient.assert_called_once_with(
        SETTINGS.search_endpoint, clients["DefaultAzureCredential"],
        api_version=SEARCH_API_VERSION,
    )


def test_create_help_does_not_touch_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_clients(monkeypatch, create)
    monkeypatch.setattr("sys.argv", ["create", "--help"])
    with pytest.raises(SystemExit) as result:
        create.main()
    assert result.value.code == 0
    create.SearchIndexClient.assert_not_called()
    create.AIProjectClient.assert_not_called()


def test_missing_index_stops_before_source_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, create)
    indexes = clients["SearchIndexClient"]
    indexes.get_index.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(ResourceNotFoundError):
        create.main()
    indexes.create_or_update_knowledge_source.assert_not_called()
    clients["AIProjectClient"].agents.create_version.assert_not_called()


def test_create_does_not_hide_search_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, create)
    indexes = clients["SearchIndexClient"]
    indexes.get_index.return_value = classic_index()
    indexes.create_or_update_knowledge_source.side_effect = HttpResponseError("denied")
    with pytest.raises(HttpResponseError, match="denied"):
        create.main()
    clients["AIProjectClient"].agents.create_version.assert_not_called()


def test_connection_uses_project_identity_and_exact_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = context_client()
    response = context_client()
    response.status_code = 200
    pipeline.send_request.return_value = response
    factory = MagicMock(return_value=pipeline)
    monkeypatch.setattr(connection, "PipelineClient", factory)
    credential = MagicMock()
    connection.manage_connection(credential, SETTINGS)
    request = pipeline.send_request.call_args.args[0]
    assert request.method == "PUT"
    assert request.url == (
        f"https://management.azure.com{SETTINGS.project_resource_id}"
        f"/connections/{SETTINGS.connection_name}?api-version=2025-10-01-preview"
    )
    assert json.loads(request.content)["properties"] == {
        "authType": "ProjectManagedIdentity",
        "category": "RemoteTool",
        "target": SETTINGS.mcp_endpoint,
        "isSharedToAll": True,
        "audience": "https://search.azure.com/",
        "metadata": {"ApiType": "Azure"},
    }
    assert len(factory.call_args.kwargs["policies"]) == 2
    response.status_code = 403
    response.text.return_value = "denied"
    with pytest.raises(HttpResponseError, match="denied"):
        connection.manage_connection(credential, SETTINGS)


def test_connection_delete_is_idempotent(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    pipeline = context_client()
    response = context_client()
    response.status_code = 404
    pipeline.send_request.return_value = response
    monkeypatch.setattr(connection, "PipelineClient", MagicMock(return_value=pipeline))
    connection.manage_connection(MagicMock(), SETTINGS, delete=True)
    assert pipeline.send_request.call_args.args[0].method == "DELETE"
    assert "already absent" in capsys.readouterr().out


def test_retrieve_only_prints_references_and_activity(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    clients = patch_clients(monkeypatch, invoke)
    monkeypatch.setattr("sys.argv", ["invoke", "--retrieve-only", "Compare recovery guidance"])
    result = {"response": [], "references": [{"id": "0"}], "activity": [{"type": "searchIndex"}]}
    clients["KnowledgeBaseRetrievalClient"].retrieve.return_value.as_dict.return_value = result
    invoke.main()
    assert json.loads(capsys.readouterr().out) == result
    request = clients["KnowledgeBaseRetrievalClient"].retrieve.call_args.args[0].as_dict()
    assert request == {
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": "Compare recovery guidance"}],
        }],
        "includeActivity": True,
        "maxRuntimeInSeconds": 120,
    }
    clients["AIProjectClient"].get_openai_client.assert_not_called()
    invoke.KnowledgeBaseRetrievalClient.assert_called_once_with(
        SETTINGS.search_endpoint, clients["DefaultAzureCredential"],
        knowledge_base_name=SETTINGS.knowledge_base_name, api_version=SEARCH_API_VERSION,
    )


@pytest.mark.parametrize("version", [None, "3"])
def test_invoke_agent_reference(monkeypatch: pytest.MonkeyPatch, capsys, version: str | None) -> None:
    clients = patch_clients(monkeypatch, invoke)
    client = context_client()
    clients["AIProjectClient"].get_openai_client.return_value = client
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text="Answer [0] with source", error=None
    )
    argv = ["invoke", "What is our policy?"]
    if version:
        argv += ["--version", version]
    monkeypatch.setattr("sys.argv", argv)
    invoke.main()
    reference = {"name": SETTINGS.agent_name, "type": "agent_reference"}
    if version:
        reference["version"] = version
    client.responses.create.assert_called_once_with(
        input="What is our policy?", extra_body={"agent_reference": reference}
    )
    assert "Answer [0]" in capsys.readouterr().out


@pytest.mark.parametrize("status,text", [("failed", ""), ("incomplete", ""), ("completed", "")])
def test_agent_errors_are_not_success(monkeypatch: pytest.MonkeyPatch, status: str, text: str) -> None:
    clients = patch_clients(monkeypatch, invoke)
    client = context_client()
    clients["AIProjectClient"].get_openai_client.return_value = client
    client.responses.create.return_value = SimpleNamespace(
        status=status, output_text=text, error="tool failure", incomplete_details=None
    )
    monkeypatch.setattr("sys.argv", ["invoke"])
    with pytest.raises(RuntimeError):
        invoke.main()


def test_empty_question_fails_before_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, invoke)
    monkeypatch.setattr("sys.argv", ["invoke", " "])
    with pytest.raises(SystemExit):
        invoke.main()
    clients["AIProjectClient"].get_openai_client.assert_not_called()


@pytest.mark.parametrize("delete_knowledge", [False, True])
def test_cleanup_preserves_index_and_shared_objects_by_default(
    monkeypatch: pytest.MonkeyPatch, delete_knowledge: bool
) -> None:
    clients = patch_clients(monkeypatch, delete)
    manage = MagicMock()
    monkeypatch.setattr(delete, "manage_connection", manage)
    argv = ["delete", "--version", "2"]
    if delete_knowledge:
        argv += ["--delete-knowledge"]
    monkeypatch.setattr("sys.argv", argv)
    delete.main()
    clients["AIProjectClient"].agents.delete_version.assert_called_once_with(
        agent_name=SETTINGS.agent_name, agent_version="2"
    )
    indexes = clients["SearchIndexClient"]
    if delete_knowledge:
        indexes.delete_knowledge_base.assert_called_once_with(SETTINGS.knowledge_base_name)
        assert [call.args[0] for call in indexes.delete_knowledge_source.call_args_list] == (
            list(SETTINGS.source_names)
        )
        manage.assert_called_once_with(clients["DefaultAzureCredential"], SETTINGS, delete=True)
    else:
        manage.assert_not_called()
        indexes.delete_knowledge_base.assert_not_called()
    indexes.delete_index.assert_not_called()
    indexes.create_or_update_index.assert_not_called()


def test_cleanup_does_not_hide_denied_agent_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = patch_clients(monkeypatch, delete)
    clients["AIProjectClient"].agents.delete_version.side_effect = HttpResponseError("denied")
    monkeypatch.setattr("sys.argv", ["delete", "--version", "2", "--delete-knowledge"])
    with pytest.raises(HttpResponseError, match="denied"):
        delete.main()
    clients["SearchIndexClient"].delete_knowledge_base.assert_not_called()


class JsonResponse(HttpResponse):
    def __init__(self, request, body: dict) -> None:
        super().__init__(request, None)
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.content_type = "application/json"
        self._body = json.dumps(body).encode()

    def body(self) -> bytes:
        return self._body

    def json(self) -> dict:
        return json.loads(self._body)


class RecordingTransport(HttpTransport):
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        return JsonResponse(request, self.response)


def test_real_sdk_requests_use_preview_and_knowledge_base_name() -> None:
    transport = RecordingTransport({"response": [], "references": [], "activity": []})
    with KnowledgeBaseRetrievalClient(
        SETTINGS.search_endpoint, AzureKeyCredential("offline-test"),
        knowledge_base_name=SETTINGS.knowledge_base_name,
        api_version=SEARCH_API_VERSION, transport=transport,
    ) as client:
        result = client.retrieve(KnowledgeBaseRetrievalRequest(include_activity=True))
    assert result.references == []
    request = transport.requests[0]
    assert request.url == (
        f"{SETTINGS.search_endpoint}/knowledgebases('{SETTINGS.knowledge_base_name}')"
        f"/retrieve?api-version={SEARCH_API_VERSION}"
    )
    assert json.loads(request.body)["includeActivity"] is True


def test_real_sdk_sends_etag_for_additive_index_update() -> None:
    index = classic_index()
    transport = RecordingTransport(index.as_dict())
    with SearchIndexClient(
        SETTINGS.search_endpoint, AzureKeyCredential("offline-test"),
        api_version=SEARCH_API_VERSION, transport=transport,
    ) as client:
        knowledge.ensure_semantic_configuration(client, SETTINGS)
    assert len(transport.requests) == 2
    request = transport.requests[1]
    assert request.headers["If-Match"] == '"revision-1"'
    payload = json.loads(request.body)
    assert payload["fields"] == index.as_dict()["fields"]
    assert payload["semantic"]["configurations"][0]["name"] == SEMANTIC_CONFIGURATION
