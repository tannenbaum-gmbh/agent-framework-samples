# Enterprise architect A2A prompt agent

This sample creates a persistent Microsoft Foundry **prompt agent** that acts as
an enterprise cloud architect. It combines two managed tools:

- **Agent2Agent (A2A) 1.0** delegates Microsoft product and Azure documentation
  questions to [`mslearn-prompt-agent`](../mslearn-prompt-agent).
- **Foundry file search** retrieves fictional internal cloud architecture
  requirements from [`internal-guidelines/`](internal-guidelines).

The result demonstrates specialist-agent composition without hosting application
code. Foundry runs the prompt, A2A, retrieval, and model tool loop.

> The included internal guidelines are fictional demonstration content. Review
> and replace them before using this pattern for a real organization.

## Prerequisites

- Complete the setup in [`mslearn-prompt-agent`](../mslearn-prompt-agent), run
  `python -m app.create`, and leave that agent deployed with incoming A2A enabled.
- A Microsoft Foundry project and a tool-capable model deployment such as GPT-5.
- Python 3.10 or newer and `az login`.
- **Foundry User** on the calling project.
- **Foundry Project Manager** to create the A2A project connection.

The easiest demonstration uses one Foundry project for both agents.

## Create the A2A connection

The architect agent references a reusable project connection rather than storing
endpoint credentials in its definition.

1. In Foundry, open the project and select **Tools** > **Connect tool**.
2. Select **Custom** > **Agent2Agent (A2A)**.
3. Name the connection `mslearn-prompt-agent-a2a`.
4. Set the endpoint to:

   ```text
   https://<account>.services.ai.azure.com/api/projects/<project>/agents/mslearn-prompt-agent/endpoint/protocols/a2a
   ```

5. Choose agentic identity authentication and audience
   `https://ai.azure.com`.
6. Do not specify an agent card path. Foundry resolves it and negotiates A2A 1.0.
7. Create the enterprise architect agent as described below. This provisions its
   agent identity.
8. On the project containing `mslearn-prompt-agent`, open **Access control
   (IAM)** and add a role assignment.
9. Assign **Foundry Agent Consumer** (or a higher Foundry role) to the
   `enterprise-architect-prompt-agent` identity.
10. Allow a few minutes for the role assignment to propagate before invoking
    the enterprise architect agent.

The role assignment in steps 8–9 is required even when both agents are in the
same project. Without it, Foundry cannot retrieve the authenticated agent card
and the A2A call fails with `Failed to fetch agent card` and HTTP 404.

## Configure

```bash
cd samples/enterprise-architect-prompt-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in the project endpoint and connection name, then export the settings.
export $(grep -v '^#' .env | xargs)
```

`FOUNDRY_PROJECT_ENDPOINT` uses this format:

```text
https://<resource>.services.ai.azure.com/api/projects/<project>
```

`FOUNDRY_MODEL_DEPLOYMENT` defaults to `gpt-5`.
`MSLEARN_A2A_CONNECTION_NAME` is the connection created above.

## Review the internal guidelines

The upload set deliberately contains precise, retrievable requirement IDs:

| Document | Example requirements |
| --- | --- |
| [`platform-security.md`](internal-guidelines/platform-security.md) | Managed identity, private endpoints, TLS, logging, exception approval |
| [`reliability-and-recovery.md`](internal-guidelines/reliability-and-recovery.md) | Workload tiers, availability, RTO/RPO, regional recovery, restore tests |
| [`governance-and-cost.md`](internal-guidelines/governance-and-cost.md) | IaC, tags, budgets, cost review, architecture approval triggers |

Edit or add Markdown files before creation to demonstrate a different internal
policy set. Every `*.md` file in the directory is uploaded.

## Create the architect agent

```bash
python -m app.create
```

The command:

1. resolves the configured A2A connection;
2. creates a Foundry vector store;
3. uploads and indexes every internal guideline;
4. creates an immutable prompt-agent version with A2A and file search tools; and
5. prints the agent version and vector store ID needed for cleanup.

If upload or agent creation fails, the command deletes the newly created vector
store rather than leaving an orphan.

## Demonstrate both tools

Ask a question that needs internal policy and Microsoft documentation:

```bash
python -m app.invoke \
  "Design a tier-1 Azure application platform. Which internal controls apply, and which Azure services support the required availability and private networking?"
```

Also try questions that isolate each specialist:

```bash
python -m app.invoke "What are our RTO, RPO, and restore-test requirements for tier 1?"
python -m app.invoke "Ask the documentation agent how Azure availability zones work."
```

The combined answer should cite internal filenames and requirement IDs while
preserving Microsoft Learn links returned by the remote A2A agent.

## Delete sample resources

Use the values printed by `app.create`:

```bash
python -m app.delete --version 1 --vector-store vs_...
```

Omit `--vector-store` only when another agent version still uses that store.

## References

- [Connect to an A2A agent endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)
- [Enable incoming A2A on a Foundry agent](https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint)
- [File search tool for agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/file-search)
