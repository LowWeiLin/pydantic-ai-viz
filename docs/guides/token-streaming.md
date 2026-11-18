# Token streaming

By default the viewer shows a model response when the request **ends**. With streaming on, the response text, the model's thinking and tool-call arguments fill in token by token while the request is still running.

```python
import pydantic_ai_viz as pviz

session = pviz.serve(agent, stream=True)
```

```bash
pydantic-ai-viz run --stream my_script.py
```

The bundled demos always stream, so `pydantic-ai-viz demo` shows what it looks like.

!!! warning "Streaming changes how your agent calls the model"

    Turning this on makes every model request of the affected agents a **streaming** request, including runs started with plain `agent.run()` or `agent.run_sync()`. A model that cannot stream then fails. With a `FunctionModel` that has no `stream_function`, for example, the run raises:

    ```text
    AssertionError: FunctionModel must receive a 'stream_function' to support streamed requests
    ```

    That is why `stream` is `False` unless you ask for it.

## Why it works this way

OpenTelemetry spans carry a finished response, never single tokens. To see tokens, pydantic-ai-viz registers an event listener on the agent with Pydantic AI's public `agent.on_event(...)`. Pydantic AI can only deliver token events from a streamed response, so the presence of any listener switches the agent's model requests to streaming. This is Pydantic AI's behaviour, not a choice made here, and it applies to any `on_event` listener you add yourself.

Real provider models (OpenAI, Anthropic, Google and the rest) support streaming, so in most applications turning this on is harmless. The cases to watch are:

- a `FunctionModel` in your tests that only defines the non-streaming function,
- a custom `Model` subclass that does not implement `request_stream`,
- code that depends on a response arriving in one piece from the provider, for example a gateway that bills or logs streamed and non-streamed calls differently.

`TestModel` streams, so it is fine.

## What is streamed

| Part of the response | Shown as |
|---|---|
| Text | Appended to the **Response** tab of the running model request |
| Thinking | Appended to a thinking block in the same tab |
| Tool call | The tool name at once, then the argument JSON as it is produced |

When the request ends, the streamed text is replaced by the final response from the span. If the two ever differ, the final one is right.

Tokens belong to the model request that produced them. When an agent delegates to another agent, the sub-agent's tokens appear under the sub-agent's own model request, not under the parent's.

## Which agents stream

The listener is attached to the agents the session knows about: the ones you passed to `serve` or `record`, the ones in a module you passed, the ones added with `session.register(...)`, and the agents they delegate to as far as the code shows.

An agent the session only learns about from its runs is captured as usual (its spans are process-wide), but without token streaming. Pass it as a target if you want its tokens.

## Turning it off again

Two things to know:

- **The listener stays on the agent** for the life of the process. Pydantic AI has no public way to remove one. After the session closes it forwards nothing, but the agent keeps making streaming requests until the process exits.
- A later session with `stream=False` in the same process does not show tokens, but agents that were streamed earlier still make streaming requests, for the same reason.

In a test suite, keep `stream=True` to the tests that are about streaming, and use models that can stream there.

## Streaming and content capture

Token streaming carries response text by nature. If you turn content capture off with `include_content=False`, leave `stream` off too. See [Privacy](privacy.md).
