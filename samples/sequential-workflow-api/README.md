# Sequential Workflow API sample

This sample shows a 2-agent **sequential workflow** built with
[Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
(`SequentialBuilder`) and exposed as a **FastAPI** endpoint, so it can be called from
outside processes. Its main purpose is to demonstrate/test that two different
users/threads can run the workflow **concurrently**, each with its own conversation
history.

## Workflow

```
user prompt -> [researcher agent] -> [writer agent] -> final answer
```

1. **researcher** — reads the user's question (and prior conversation for the thread)
   and produces short research notes.
2. **writer** — reads the research notes and writes the final, well-structured answer.

Both agents are backed by a GPT-5 deployment on a Microsoft Foundry project (see
[`infra/`](../../infra)) via `agent_framework.foundry.FoundryChatClient`.

## Concurrency design

- Every request builds **brand new** agent/chat-client instances and a **brand new**
  `Workflow` (`app/workflow.py::WorkflowService.run`). There is no shared, mutable
  client state between requests, so two requests for two different `thread_id`s run
  fully in parallel.
- A lightweight, in-memory, per-thread `asyncio.Lock` + conversation history
  (`ThreadState`) only serializes turns **within** the same thread, keeping a single
  conversation consistent without blocking unrelated threads.

This is validated by the tests in `tests/`, which use a fake, delayed chat client to
assert that:
- two different threads complete in roughly the time of *one* run (i.e., concurrently), and
- two calls for the *same* thread are serialized (their combined time is roughly additive).

## Setup

1. Deploy the infrastructure in [`infra/`](../../infra) (or point at an existing
   Microsoft Foundry project with a `gpt-5` deployment).
2. Copy `.env.example` to `.env` and fill in the values (Agent Framework does not load
   `.env` files automatically — export the variables into your shell, or use
   `python-dotenv`'s `load_dotenv()`):

   ```bash
   cp .env.example .env
   export $(grep -v '^#' .env | xargs)
   ```
3. In a dev container, the repository `.venv` and all runtime and test dependencies
  are created automatically. New integrated terminals use this environment by
  default.

  For local development outside the dev container, run the following commands from
  this sample directory:

   ```bash
  python -m venv ../../.venv
  source ../../.venv/bin/activate
  python -m pip install -r requirements-dev.txt
   ```
4. Authenticate to Azure (the sample uses `DefaultAzureCredential`), e.g. `az login`.

## Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

Call it:

```bash
curl -s http://localhost:8000/workflows/sequential/run \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "user-1", "message": "What is the largest city in France?"}' | jq
```

## Test concurrency manually

Run two requests for two different threads at (roughly) the same time and compare the
timestamps in the server logs/response `duration_seconds`:

```bash
curl -s http://localhost:8000/workflows/sequential/run \
  -d '{"thread_id": "user-1", "message": "Tell me about the Eiffel Tower"}' &
curl -s http://localhost:8000/workflows/sequential/run \
  -d '{"thread_id": "user-2", "message": "Tell me about the Colosseum"}' &
wait
```

Both requests should complete in roughly the same amount of time as a single request,
confirming they ran concurrently rather than being queued behind one another.

## Run the tests

```bash
pytest
```

The tests do **not** require Azure credentials or network access — they substitute a
fake chat client for the Foundry-backed one.
