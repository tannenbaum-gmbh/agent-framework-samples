# Classic AI Search RAG

This sample demonstrates the traditional **retrieve-then-generate** RAG pattern
with Azure AI Search. The application owns retrieval and assembles the model's
grounding context explicitly. It does not use Foundry file search, a knowledge
base, agentic retrieval, or Foundry IQ.

The focus is **programmatic ingestion** into existing resources:

```text
enterprise-architect-prompt-agent/internal-guidelines/*.md
    -> upload to a dedicated Blob container
    -> Blob data source
    -> indexer: extract text -> split -> embed -> project chunks
    -> AI Search chunk index

question -> hybrid search (keyword + vector) -> retrieved chunks
         -> Azure OpenAI Responses API -> answer with source references
```

The Markdown files are read directly from the
[enterprise architect sample](../enterprise-architect-prompt-agent/internal-guidelines);
there is no duplicate document set. They are fictional demonstration policies,
not Microsoft guidance or production policy. No enterprise architect agent or
Microsoft Learn A2A deployment is required.

## Prerequisites

- Python 3.10 or newer and an Azure sign-in (`az login` locally).
- An **existing Azure AI Search service** supporting indexers, skillsets, and
  vector search. Basic or higher is recommended.
- An **existing general-purpose v2 storage account** with Blob Storage.
- An Azure OpenAI resource (or the Azure OpenAI endpoint of an existing Foundry
  resource) with a deployed **`text-embedding-3-small`** model. Its deployment
  name is configurable; its model and 1,536 dimensions are fixed in this sample.
- For answer generation only: a model deployment supporting the Azure OpenAI
  v1 Responses API, such as GPT-5. No chat deployment is needed for ingestion or
  `--search-only`. The repository's infrastructure does not deploy Search,
  Storage, or the embedding model for this sample.
- A **system-assigned managed identity enabled on the Search service**.
- Entra ID data-plane authentication enabled on Search and appropriate role
  assignments below. Allow time for new assignments to propagate.

### Identity and permissions

All application clients use `DefaultAzureCredential`. There are no storage
account keys, Search admin keys, or OpenAI API keys in configuration.

| Identity | Scope | Required role |
| --- | --- | --- |
| Your CLI/application identity | Search service | **Search Service Contributor** to manage index, data source, skillset, and indexer |
| Your CLI/application identity | Search service | **Search Index Data Reader** to query chunks |
| Your CLI/application identity | Storage account | **Storage Blob Data Contributor** to create the sample container and upload/delete blobs |
| Your CLI/application identity | Azure OpenAI resource | **Cognitive Services OpenAI User** for answer generation |
| Search service system-assigned identity | Storage account | **Storage Blob Data Reader** for indexer reads |
| Search service system-assigned identity | Azure OpenAI resource | **Cognitive Services OpenAI User** for ingestion and query embeddings |

The data source uses `ResourceId=...;` to select managed-identity authentication.
The embedding skill and query vectorizer omit both API keys and an explicit
identity, so Search uses its system-assigned identity.

This sample does not change networking, enable managed identities, or assign
roles. Your workstation must reach Search, Storage, and OpenAI; Search must
reach Storage and OpenAI. If resources are firewalled, configure supported
trusted-service access or private connectivity for the indexer/vectorizer as
well as client access. A successful local upload does not prove Search can read
the same container.

## Configure

From the repository checkout:

```bash
cd samples/ai-search-rag
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env to reference your existing resources and deployments.
az login
```

Commands automatically load this sample's `.env`, independent of the current
working directory; already-exported variables take precedence. Run the commands
below from this sample directory so Python resolves its `app` package.

| Variable | Meaning |
| --- | --- |
| `AZURE_SEARCH_ENDPOINT` | Search resource endpoint, such as `https://my-search.search.windows.net` |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob endpoint, such as `https://mystorage.blob.core.windows.net` |
| `AZURE_STORAGE_RESOURCE_ID` | Full ARM ID of that same storage account, not a container ID or connection string |
| `AZURE_OPENAI_ENDPOINT` | Resource endpoint, such as `https://my-resource.openai.azure.com`; not a Foundry project URL or `/openai/v1` URL |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment name of your `text-embedding-3-small` model |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Responses-capable deployment name; only required for generation |
| `AZURE_SEARCH_RESOURCE_PREFIX` | Dedicated object prefix; defaults to `classic-rag` |

The prefix must be 3-40 lowercase letters, digits, or single hyphens, start with
a letter, and end with a letter or digit. Use a unique prefix per developer.
**Do not reuse names of existing production objects:** creation updates objects
with these names, and cleanup deletes them.

## Upload, configure, and run indexing

```bash
python -m app.create
# Optional longer wait for a cold service:
python -m app.create --timeout 1200
```

The command:

1. Finds all `*.md` guidelines in deterministic filename order.
2. Creates `<prefix>-documents` if absent and uploads each file, overwriting its
   same-named blob on subsequent runs.
3. Creates or updates the `<prefix>-chunks` index with chunk text, filename,
   source Blob URL, parent ID, and a 1,536-dimensional vector field.
4. Creates or updates `<prefix>-blobs`, a Blob data source.
5. Creates or updates `<prefix>-skills`: character-based splitting into
   2,000-character pages with 500-character overlap, Azure OpenAI embeddings,
   and one-to-many index projections. Only chunks, not empty parent records,
   are indexed. Metadata is copied onto each chunk for citation.
6. Creates or updates `<prefix>-indexer`, using the text parser for Markdown.
   New indexers run automatically; existing indexers are explicitly triggered.
7. Polls the current execution until success or the timeout. A previous run's
   success is not mistaken for this run's completion. Execution errors and
   failed documents raise errors; execution warnings are printed.

There is no recurring schedule. Rerun the command after editing the shared
guidelines to upload and index them again. Blob modification timestamps provide
incremental change detection. Runs with an already-active indexer are rejected;
wait for completion before updating the pipeline. A timeout only stops local
polling, not the Azure execution.

This is a small, additive/update demo, **not a document synchronization service**:
removing a local Markdown file does not delete its blob or indexed chunks.
For a clean rebuild (including after an incompatible schema/model change), run
cleanup with `--delete-blobs` and then recreate. A production version should add
a deliberate deletion-detection policy and document lifecycle management.

Failures leave successfully created objects in place for inspection and retry;
there is no destructive rollback of existing resources. Use the Azure portal's
indexer execution history for detailed errors. The indexer configuration
deliberately omits SDK constructor defaults: the SQL-only `queryTimeout` default
is rejected by Blob data sources. The serialization test checks the exact Blob
configuration sent to Search. A 403 usually indicates missing
RBAC or network access on one of the identity hops above; a 429 may require more
embedding quota or retrying later. Indexer success is not a search-quality
evaluation, and indexed changes can take a short time to become query-visible.

## Inspect classic retrieval

```bash
python -m app.invoke --search-only \
  "What are our tier-1 RTO, RPO, and restore-test requirements?"
```

This prints JSON containing chunk IDs, filenames, source URLs, and text. Search
combines keyword matches with vector similarity using reciprocal rank fusion.
The index's vectorizer embeds the question with the same deployment used by the
ingestion skill. No semantic ranker or agentic query planning is involved.

## Generate a grounded answer

```bash
python -m app.invoke \
  "What are our tier-1 RTO, RPO, and restore-test requirements?"

python -m app.invoke --top 3 \
  "Which governance and security controls apply before production approval?"
```

The application retrieves five chunks by default (`--top` accepts 1-20), sends
only those excerpts and the question to the model, and requests `[1]`-style
citations plus requirement IDs. It prints the retrieved source map after the
answer. For the tier-1 question, look for **1 hour RTO**, **15 minutes RPO**, and
**quarterly restore tests** from the reliability guidelines.

Source URLs identify private blobs; the sample does not make the container
public or issue SAS tokens. Accessing the underlying document still requires
authorization. Citation labels are model-generated, not independently validated.
No results, incomplete generation, and upstream service errors are surfaced
rather than falling back to an ungrounded model answer.

## Cleanup

```bash
# Delete only this prefix's indexer, skillset, data source, and index:
python -m app.delete

# Also delete the entire dedicated container and all its blobs:
python -m app.delete --delete-blobs
```

Cleanup never deletes the Search service, storage account, OpenAI resource,
model deployments, or the shared local Markdown files. Do not run it during
indexing; verify the configured prefix and container before deleting blobs.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests are offline: they serialize real SDK models and mock service calls to
verify configuration, uploads, projection/vector wiring, run triggering and
polling, failure behavior, hybrid queries, grounding context, and cleanup.
Running the commands against your configured Azure resources is still required
to validate RBAC, networking, model deployments, and end-to-end search quality.

## Follow-up: Foundry IQ

This sample deliberately establishes the classic baseline: explicit indexer
resources plus application-controlled retrieve-then-generate. A follow-up can
reuse the corpus and existing Azure resources to compare Foundry IQ knowledge
sources/knowledge bases and agentic retrieval. Those features are not configured
here, and the classic chunk index is not automatically a Foundry IQ knowledge base.

## References

- [Azure Blob indexer](https://learn.microsoft.com/azure/search/search-how-to-index-azure-blob-storage)
- [Integrated vectorization](https://learn.microsoft.com/azure/search/vector-search-integrated-vectorization)
- [Index projections](https://learn.microsoft.com/azure/search/search-how-to-define-index-projections)
- [Azure OpenAI embedding skill](https://learn.microsoft.com/azure/search/cognitive-search-skill-azure-openai-embedding)
- [Search managed identities](https://learn.microsoft.com/azure/search/search-how-to-managed-identities)
- [Azure OpenAI v1 API](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/latest)
