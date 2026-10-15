# Conditional workflow sample

This sample uses [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
to build a multi-step content workflow with a quality gate and conditional branch.

```text
request -> planner -> writer -> reviewer -- approved --> finalizer
                                      \-- revise ----> reviser -> finalizer
```

The planner creates an outline, the writer produces a draft, and the reviewer decides
whether the draft is ready. Approved content goes directly to the finalizer; content
that needs work is sent through the reviser first. `WorkflowBuilder` conditions make
the routing explicit, and `max_iterations` places a bound on workflow execution.

## Setup

1. Deploy the infrastructure in [`infra/`](../../infra), or use an existing Microsoft
   Foundry project with a model deployment.
2. Export the required environment variables:

   ```bash
   cp .env.example .env
   export $(grep -v '^#' .env | xargs)
   ```

3. From this directory, install the dependencies if they are not already available:

   ```bash
   python -m pip install -r requirements-dev.txt
   ```

4. Authenticate with Azure, for example with `az login`. The sample uses
   `DefaultAzureCredential`.

## Run

Run the module from this sample directory:

```bash
python -m app.main "Write a short explanation of conditional agent workflows for Python developers"
```

The command prints the final content and whether the revision branch ran.

## Test

```bash
pytest
```

The tests use deterministic fake chat clients, require no Azure credentials, and
verify both the direct approval path and the revision path.