# Foundry IQ: internal guidelines, Microsoft Learn, and the web

Build on [Classic AI Search RAG](../ai-search-rag/README.md) without copying its
documents or rebuilding its ingestion pipeline. This sample creates a **Foundry
IQ knowledge base** backed by three knowledge sources:

1. The **existing chunk index** created by the classic sample.
2. The **Microsoft Learn MCP server**, queried live through `microsoft_docs_search`.
3. **Web Knowledge Source**, which searches the public web using Bing Custom Search.

All three are knowledge sources **inside the same knowledge base**. The agent
has one tool, `knowledge_base_retrieve`, exposed through the knowledge base's MCP
endpoint. It does not separately orchestrate three agent tools.

```text
Existing ai-search-rag Blob -> indexer -> classic-rag-chunks (unchanged ingestion)
                                             |
                                 searchIndex knowledge source
                                             |
Microsoft Learn MCP -> mcpServer source ------+--> Foundry IQ knowledge base
Public web search ---> web source -----------+       |
                                                     | MCP / managed identity
                                                 Prompt agent
                                                     |
                                              cited recommendation
```

In classic RAG, the application retrieves chunks and assembles context. Here,
the knowledge base uses an LLM to plan and route retrieval, searches the selected
sources, and returns grounding data and references. The prompt agent synthesizes
the final answer. `--retrieve-only` exposes the retrieval response and activity
without involving the agent.

**Preview:** MCP Server knowledge sources use Search API
`2026-08-01-preview`. This sample pins `azure-search-documents==12.1.0b2` and uses
that API explicitly. These preview features can change and are not a production
SLA commitment. Use a **separate virtual environment** from the classic sample,
which uses Search SDK 11.x.

## Prerequisites

- Python 3.10+ and an Azure sign-in (`az login` locally).
- Complete [the classic sample's ingestion](../ai-search-rag/README.md#upload-configure-and-run-indexing)
  first. The populated index must be on the **same Search service** as this
  knowledge base. This sample expects `chunk_id`, `chunk`, `title`, and `source`
  fields. The classic sample's existing vectorizer and embeddings are reused.
- An Azure AI Search service in a
  [region supporting agentic retrieval](https://learn.microsoft.com/azure/search/search-region-support),
  with **semantic ranker enabled**. The classic sample's indexing requirements
  alone do not guarantee that agentic retrieval is supported.
- An existing Foundry project with a **system-assigned managed identity**, and
  a supported deployed chat model (for example `gpt-5`). The same deployment
  serves the knowledge base's query planning/web summarization and the agent's
  final answer generation. Its resource endpoint must be supplied separately
  from the project endpoint.
- A system-assigned identity on the Search service. Preserve its existing
  embedding-model permissions from the classic sample.
- [Web Knowledge Source access](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-web-manage)
  enabled for the subscription. It is enabled by default, but administrators can
  disable it. Web Knowledge Source requires a supported public Azure region,
  not a private or sovereign cloud. No separate Bing resource, key, or project
  connection is needed for this knowledge source.
- Outbound HTTPS reachability from Search to Microsoft Learn, the web service,
  and the model endpoint; Foundry must reach the Search knowledge-base MCP
  endpoint. Your workstation must reach ARM, Search, and Foundry. Configure
  supported private connectivity/trusted-service access where applicable.

### Identity and permissions

All local clients use `DefaultAzureCredential`. The new project connection uses
`ProjectManagedIdentity`, **not** a Search API key or a persisted user token.

| Identity | Scope | Required role |
| --- | --- | --- |
| Your CLI/application identity | Search service | **Search Service Contributor** to read/update the index and manage knowledge objects |
| Your CLI/application identity | Search service | **Search Index Data Reader** for `--retrieve-only` |
| Your CLI/application identity | Foundry account/project | **Foundry User** (formerly Azure AI User) to create/invoke/delete agents; **Foundry Project Manager** (formerly Azure AI Project Manager), or equivalent permission, to manage the connection |
| Foundry **project** system-assigned identity | Search service | **Search Index Data Reader** to retrieve through the knowledge-base MCP endpoint |
| Search service system-assigned identity | Chat-model resource | **Cognitive Services User** for the knowledge base's LLM calls |
| Search service system-assigned identity | Embedding-model resource | Keep the classic sample's **Cognitive Services OpenAI User** assignment for query vectorization |

This sample creates knowledge objects, the project connection, and an agent
version; it does **not** provision services, deploy models, enable identities,
or assign roles. Allow time for role assignments to propagate.

### External data and costs

This is a **fictional-policy demonstration**, using the same shared architecture
guidelines as the classic sample. Both the question and retrieval conversation
can influence external MCP/web queries. Instructions request generic external
queries and prohibit forwarding internal text, secrets, or identifiers, but
**prompt instructions are not a data-loss-prevention boundary**. Do not use
sensitive documents or questions with this sample. A production design must
enforce outbound-data policy independently of the model.

Web Knowledge Source uses Bing services subject to the
[Grounding with Bing terms](https://www.microsoft.com/bing/apis/grounding-legal-enterprise)
and [Microsoft Privacy Statement](https://go.microsoft.com/fwlink/?LinkId=521839&clcid=0x409).
The Data Protection Addendum does not apply to data sent to Grounding with Bing;
data can leave Azure compliance/geographic boundaries. Review these terms before
running. Search semantic ranking/agentic retrieval, model calls, and web queries
can incur charges. External results are untrusted reference material.

## Configure

From the repository checkout:

```bash
cd samples/foundry-iq
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with the existing Search and Foundry resources.
az login
```

Commands load **this sample's** `.env`; exported variables take precedence.
They do not automatically read the classic sample's `.env`. Run commands from
this directory to resolve the local `app` package.

| Variable | Meaning |
| --- | --- |
| `AZURE_SEARCH_ENDPOINT` | The classic sample's Search resource endpoint |
| `AZURE_SEARCH_RESOURCE_PREFIX` | Classic prefix; defaults to `classic-rag`, selecting `classic-rag-chunks` |
| `AZURE_SEARCH_INDEX_NAME` | Optional explicit index override, taking precedence over the classic prefix |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `FOUNDRY_PROJECT_RESOURCE_ID` | Full ARM ID of that same project; required for create and shared-knowledge cleanup, not invocation |
| `AZURE_OPENAI_ENDPOINT` | Resource endpoint hosting the chat model, such as `https://<account>.openai.azure.com`; no `/openai/v1` path |
| `FOUNDRY_MODEL_DEPLOYMENT` | Supported chat deployment name, used for retrieval planning and agent generation |
| `FOUNDRY_MODEL_NAME` | Actual model name, default `gpt-5`; not necessarily the deployment name |
| `FOUNDRY_IQ_RESOURCE_PREFIX` | Dedicated prefix for new objects, default `foundry-iq` |

No Storage URL/resource ID or embedding deployment variable is needed here:
ingestion and vectorization already belong to the existing index.

Use a unique IQ prefix per developer. It must be 2-40 lowercase letters, digits,
or single hyphens, starting and ending with a letter or digit. **Creation updates
same-named knowledge objects and the connection.** Do not use production names.
Ensure the endpoint and ARM ID identify the same Foundry project.

## Create

```bash
python -m app.create
```

The command:

1. Reads the existing index and checks its required fields. A missing or
   incompatible index fails rather than creating a replacement.
2. Adds `foundry-iq-semantic`, prioritizing `chunk`, unless it already exists.
   Existing fields, vectors, semantic configurations, and the default semantic
   configuration are preserved. The update uses the index ETag to reject
   concurrent changes. The knowledge source explicitly selects this configuration.
3. Creates/updates `<iq-prefix>-guidelines`, `<iq-prefix>-mslearn`, and
   `<iq-prefix>-web`. Only the read-only Learn search tool is enabled; public
   web search has no domain restriction.
4. Creates/updates `<iq-prefix>-kb`, referencing all three sources. Retrieval uses
   **low** reasoning effort (MCP sources do not support minimal) and returns
   **extractive grounding data** rather than synthesizing the final answer.
5. Creates/updates `<iq-prefix>-connection` through the ARM RemoteTool preview
   API, targeting the knowledge-base MCP endpoint with the Search token audience.
6. Creates a new version of `<iq-prefix>-agent`, using that connection and only
   `knowledge_base_retrieve`. The version is printed for invocation and cleanup.
   Automatic approval is limited to this trusted, read-only retrieval tool.

No blobs, documents, indexers, skills, embedding deployments, or service
infrastructure are created or deleted. There is no rollback: failures leave
completed objects available for inspection and retry. Rerunning creates a new
agent version but updates the shared knowledge objects in place.

The index is the one intentional shared mutation: adding a semantic configuration
does not remove classic RAG functionality. If you rerun the **classic ingestion
create command**, it can replace the index's semantic settings; rerun this
sample's create command afterward. Do not run both create commands concurrently.

## Compare retrieval and invoke

```bash
# Inspect references and the query/source activity, without agent synthesis.
python -m app.invoke --retrieve-only \
  "Compare our tier-1 recovery policy with Azure disaster recovery guidance and recent Azure recovery announcements."

# Ask the agent to produce a cited comparison.
python -m app.invoke \
  "What do our internal guidelines require for private endpoints, and how does Microsoft Learn recommend implementing them?"

# Exercise the public-web route.
python -m app.invoke --retrieve-only \
  "What are the latest public announcements about Azure disaster recovery?"
```

The agent command prints the answer with the model's citation markers/source
links preserved. `--retrieve-only` prints the complete retrieval JSON, including
references and activity records, to inspect source selection, evidence, and
partial failures. It uses your identity; agent retrieval uses the project's
identity, so one succeeding does not prove permissions for the other.

Source selection is model-driven: **not every question calls every source**.
Inspect retrieval activity rather than treating a plausible answer as proof that
all sources ran. Test internal-only, Learn-only, web-current, and combined
questions; check that internal statements cite the guideline fields and external
statements cite real URLs. No deterministic assertion of live source selection or
answer quality is made by the offline tests.

Troubleshooting:

- **403:** Check the particular identity hop and network path, including project
  versus account identity. A local Search query does not validate agent access.
- **Unsupported API/region/model:** Confirm agentic retrieval, MCP preview support,
  semantic ranker, and the actual underlying model name. Do not silently fall back
  to classic RAG.
- **Missing index/configuration:** Run classic ingestion first, then this sample's
  create command. An ETag conflict means another process changed the index; inspect
  that change before retrying.
- **Web disabled, MCP timeout, or 429:** Check subscription policy, endpoint
  reachability, and quotas. The inspect command allows 120 seconds for retrieval;
  service/agent tool timeouts can still apply.
- **Weak/missing evidence:** Inspect references and activity, refine the question,
  and verify actual source content. There is no fallback to invented policy.

## Cleanup

```bash
# Safe default: delete only the printed agent version.
python -m app.delete

# After deleting every dependent agent version, remove the shared IQ objects too.
python -m app.delete --delete-knowledge
```

`--delete-knowledge` also deletes the project connection, knowledge base, and
three knowledge sources. They are **shared by all versions**: this breaks any
other agent still referencing them, even when a different agent version is pinned.
Delete dependent versions first. Already-absent objects are reported explicitly;
authorization/service failures stop cleanup.

Cleanup **never deletes or modifies the original index**, its additive semantic
configuration, Blob container/content, data source, indexer, or skillset. This
deliberately preserves the classic example. To remove its resources, use that
sample's cleanup only after all IQ references have been removed.

## Offline tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover configuration, real SDK serialization of all three sources, source
and model wiring, additive/ETag-protected semantic updates, ARM connection payload,
lifecycle commands, failure propagation, citation-bearing output, and
non-destructive cleanup. In-memory HTTP transports also verify actual SDK request
URLs, preview API versions, retrieval payloads, and conditional update headers.
They do not provision resources or verify live Azure permissions, networking,
external-source availability, or answer quality.

## References

- [Foundry IQ overview](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-foundry-iq)
- [Search index criteria for agentic retrieval](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-create-index)
- [Search index knowledge source](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-search-index)
- [MCP Server knowledge source](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-mcp-server)
- [Web Knowledge Source](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-web)
- [Create a knowledge base](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-create-knowledge-base)
- [Connect Foundry IQ to Agent Service](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect)
