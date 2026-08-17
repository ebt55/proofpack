"""Gemini plumbing shared by the research agent and chat mode.

One place owns the model loop so both callers behave the same way:

  - tools are plain Python callables (signature + docstring become the function
    declaration); automatic function calling is *disabled* so this code, not the
    SDK, executes each call — that is where the integrity gates live;
  - the Chat object keeps the conversation (and Gemini 3 thought signatures) so
    every turn sees the full history;
  - token usage is accumulated per turn for the cost line in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from google import genai
from google.genai import types

from .models import TokenUsage


@dataclass
class LoopResult:
    text: str                 # last non-empty text the model produced (the summary)
    iterations: int           # model responses consumed
    usage: TokenUsage = field(default_factory=TokenUsage)


def make_client() -> genai.Client:
    """Client that reads GEMINI_API_KEY / GOOGLE_API_KEY and retries 429/5xx with backoff."""
    return genai.Client(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=6, initial_delay=2.0, max_delay=30.0, exp_base=2.0, jitter=1.0
            )
        )
    )


def new_chat(
    client: genai.Client,
    model: str,
    system: str,
    tools: list[Callable],
    *,
    max_output_tokens: int = 8192,
) -> genai.chats.Chat:
    """A chat session with the tools declared and automatic execution turned off."""
    return client.chats.create(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=system,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=max_output_tokens,
        ),
    )


def _text_of(resp: types.GenerateContentResponse) -> str:
    """Concatenate the text parts of a response without tripping on function-call parts."""
    out: list[str] = []
    for cand in resp.candidates or []:
        content = cand.content
        for part in (content.parts if content and content.parts else []):
            if getattr(part, "text", None) and not getattr(part, "thought", False):
                out.append(part.text)
    return "\n".join(s for s in out if s.strip()).strip()


def run_tool_loop(
    chat: genai.chats.Chat,
    tools: list[Callable],
    first_message,
    *,
    max_iterations: int,
    log: Callable[[str], None] = print,
    usage: Optional[TokenUsage] = None,
) -> LoopResult:
    """Send a message and keep executing tool calls until the model stops asking.

    Every tool call the model makes is executed here in Python and the result is
    returned as a function response; nothing is executed automatically by the SDK.
    """
    by_name = {t.__name__: t for t in tools}
    usage = usage if usage is not None else TokenUsage()
    iterations = 0
    text = ""

    resp = chat.send_message(first_message)
    while True:
        iterations += 1
        usage.add(resp.usage_metadata)
        latest = _text_of(resp)
        if latest:
            text = latest
        calls = list(resp.function_calls or [])
        if not calls:
            break
        if iterations >= max_iterations:
            log(f"agent: iteration cap ({max_iterations}) reached, stopping")
            break
        parts = []
        for fc in calls:
            fn = by_name.get(fc.name or "")
            if fn is None:
                result = f"REJECTED: unknown tool {fc.name!r}. Available: {', '.join(by_name)}"
            else:
                try:
                    result = fn(**(dict(fc.args) if fc.args else {}))
                except TypeError as e:  # bad/missing arguments — tell the model, don't crash
                    result = f"REJECTED: bad arguments for {fc.name}: {e}"
            parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": str(result)})
            )
        resp = chat.send_message(parts)

    return LoopResult(text=text, iterations=iterations, usage=usage)
