# Microsoft Learn MCP prompt agent

This sample creates a persistent Microsoft Foundry **prompt agent** that uses the
public Microsoft Learn MCP server and exposes both Responses and Agent2Agent
(A2A) endpoints. It intentionally mirrors the instructions and tool behavior in
[`mslearn-hosted-agent`](../mslearn-hosted-agent) while also acting as the remote
documentation specialist for
[`enterprise-architect-prompt-agent`](../enterprise-architect-prompt-agent).

## What this sample demonstrates

The prompt, model reference, and MCP tool configuration are stored as a versioned
resource in Foundry Agent Service. Foundry runs the model-and-tool loop; there is
no application container to build, registry to manage, or agent process to host.
After creation, the agent appears in the Foundry portal and remains there until
you delete its version. The create command also publishes an agent card and
enables incoming A2A 1.0 requests.

| Characteristic | Prompt agent | Hosted agent |
| --- | --- | --- |
| Agent logic runs in | Foundry-managed prompt runtime | Your container, managed by Foundry |
| Definition contains | Model, instructions, and tools | Container image, resources, environment, and protocols |
| Custom application code at runtime | No | Yes |
| Container build and registry | Not required | Required |
| Framework/runtime flexibility | Foundry prompt-agent runtime | Your framework and application runtime |
| Portal visibility | Immediately after `create_version` | After the hosted version is deployed |
| Best fit | Declarative agents and fast iteration | Custom orchestration, dependencies, protocols, or runtime control |

## Prerequisites

- A Microsoft Foundry project and a tool-capable model deployment such as GPT-5.
- Python 3.10 or newer.
- `az login`.
- Permission to create agent versions and invoke the model in the project.
- Outbound access from Foundry to `https://learn.microsoft.com/api/mcp`.

The Microsoft Learn MCP server is public, so this sample does not require a
project connection or secret. Tool approval is set to `never` because the tool
only retrieves public documentation.

## Configure

```bash
cd samples/mslearn-prompt-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in the project endpoint, then export the settings.
export $(grep -v '^#' .env | xargs)
```

`FOUNDRY_PROJECT_ENDPOINT` must identify the same project you open in the portal:

```text
https://<resource>.services.ai.azure.com/api/projects/<project>
```

`FOUNDRY_MODEL_DEPLOYMENT` defaults to `gpt-5`.

## Create the prompt agent

```bash
python -m app.create
```

The command creates a new immutable version of `mslearn-prompt-agent` and leaves
it deployed, then updates the stable agent endpoint to enable A2A. Open that
project in the Foundry portal and select **Build** >
**Agents** to inspect its model, instructions, Microsoft Learn MCP tool, and
version.

Run the command again after changing the definition to create another version.
Use `--name` when you want a different resource name:

```bash
python -m app.create --name customer-demo-prompt-agent
```

## Invoke it

Invoke the stored agent definition through the Foundry Responses API:

```bash
python -m app.invoke \
  "What are Microsoft Foundry hosted agents, and when should I use them?"
```

If you used a custom name, pass the same name to `app.invoke`:

```bash
python -m app.invoke --name customer-demo-prompt-agent "What is Agent Framework?"
```

You can also test the persistent agent from the Foundry playground. Both paths
use the server-side model, instructions, and MCP tool shown in the portal.

## Use the A2A endpoint

The authenticated A2A base path is:

```text
https://<account>.services.ai.azure.com/api/projects/<project>/agents/mslearn-prompt-agent/endpoint/protocols/a2a
```

Callers need the **Foundry Agent Consumer** role or higher on this project. For a
Foundry A2A connection, use the base path above, the `agentic-identity`
authentication type, and audience `https://ai.azure.com`. Do not append an agent
card path; Foundry discovers and negotiates the A2A 1.0 agent card.

The Python SDK currently configures incoming A2A rather than the Foundry portal.
Running `app.create` again preserves the endpoint configuration while adding a
new immutable agent version.

## Delete a version

Use the version printed by `app.create`:

```bash
python -m app.delete --version 1
```

For a custom name:

```bash
python -m app.delete --name customer-demo-prompt-agent --version 1
```

## Suggested customer demo

1. Run the local CLI in [`mslearn-hosted-agent`](../mslearn-hosted-agent) and show
   that it is an in-process agent and does not create a portal resource.
2. Run `python -m app.create` here and refresh **Build** > **Agents** to show the
   persistent prompt-agent definition immediately.
3. Ask the same question through both CLIs to show equivalent Microsoft Learn
   grounding.
4. Create the enterprise architect sample and ask it a Microsoft documentation
   question to show the A2A delegation.
5. Open the prompt agent in the playground and inspect its declarative model,
   instructions, MCP tool, agent card, and versions.
6. Contrast that with the hosted-agent deployment, where Foundry runs your
   container and exposes its Responses endpoint.

## Files

| Path | Purpose |
| --- | --- |
| `app/agent.py` | Prompt-agent definition and shared Microsoft Learn behavior. |
| `app/create.py` | Create and persist an immutable prompt-agent version. |
| `app/invoke.py` | Invoke the named prompt agent through the Responses API. |
| `app/delete.py` | Delete a specific prompt-agent version. |
| `app/config.py` | Read and validate environment configuration. |

## References

- [Quickstart: Create a prompt agent](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-create-prompt-agent)
- [Connect agents to Model Context Protocol servers](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol)
- [Enable incoming A2A on a Foundry agent](https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint)
- [Connect to an A2A agent endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)
- [Foundry hosted agents](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent)
