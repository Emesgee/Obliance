"""Provider adapters — the only code that imports the vendor SDK (gate G-04).

    anthropic   Anthropic first-party API, ZDR, inference_geo pinned (ADR-0008 §3)
    vertex_eu   Claude on Google Vertex AI, EU region — Google is the processor
    fake        scripted responses for tests and a keyless dev box

All three take the same ProviderRequest and return the same ProviderResponse, so
app.llm.client is identical across backends (ADR-0008: "vertex_eu answers the
same contract test suite as default").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.core.config import settings


class LlmError(RuntimeError):
    """Base for everything the LLM layer raises. `code` is stable for callers."""

    code = "llm_error"


class LlmNotConfigured(LlmError):
    code = "llm_not_configured"


class LlmProviderError(LlmError):
    code = "llm_provider_error"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    model: str
    effort: str
    max_tokens: int
    system: str
    material: str  # the packed <materiale> block — cached prefix
    question: str  # the varying tail
    output_schema: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    inference_geo: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    stop_reason: str
    model: str
    usage: ProviderUsage


class Provider(Protocol):
    name: str

    def complete(self, req: ProviderRequest) -> ProviderResponse: ...


# ---- Anthropic (first-party and Vertex share the SDK surface) --------------------------------


class AnthropicProvider:
    def __init__(self, name: str, client: Any) -> None:
        self.name = name
        self._client = client

    def _params(self, req: ProviderRequest, *, with_effort: bool) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # Stable prefix first, cached (ADR-0009 §4); question after the break.
                        {
                            "type": "text",
                            "text": req.material,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": req.question},
                    ],
                }
            ],
            "timeout": settings.llm_timeout_seconds,
        }
        output_config: dict[str, Any] = {}
        if with_effort:
            params["thinking"] = {"type": "adaptive", "display": "omitted"}
            output_config["effort"] = req.effort
        if req.output_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": anthropic.transform_schema(req.output_schema),
            }
        if output_config:
            params["output_config"] = output_config
        if self.name == "anthropic":
            params["inference_geo"] = settings.llm_inference_geo
            # ADR-0009 §5: a policy decline is re-run server-side on Anthropic's
            # category-routed fallback instead of failing the run. Beta, first-party only.
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    def _create(self, params: dict[str, Any]) -> Any:
        if "betas" in params:
            return self._client.beta.messages.create(**params)
        return self._client.messages.create(**params)

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        try:
            try:
                msg = self._create(self._params(req, with_effort=True))
            except anthropic.BadRequestError as e:
                # A model without adaptive thinking / effort: retry once without them.
                if "effort" not in str(e) and "thinking" not in str(e):
                    raise
                msg = self._create(self._params(req, with_effort=False))
        except anthropic.APIError as e:
            raise LlmProviderError(f"{self.name}: {e.__class__.__name__}: {e}") from e
        text = "".join(getattr(block, "text", "") for block in msg.content if block.type == "text")
        u = msg.usage
        return ProviderResponse(
            text=text,
            stop_reason=str(msg.stop_reason or ""),
            model=str(msg.model),
            usage=ProviderUsage(
                input_tokens=int(u.input_tokens),
                output_tokens=int(u.output_tokens),
                cache_read_tokens=int(u.cache_read_input_tokens or 0),
                cache_write_tokens=int(u.cache_creation_input_tokens or 0),
                inference_geo=getattr(u, "inference_geo", None),
            ),
        )


def _anthropic() -> AnthropicProvider:
    if not settings.anthropic_api_key:
        raise LlmNotConfigured("ANTHROPIC_API_KEY mangler (ADR-0008: nøglen lever kun på serveren)")
    return AnthropicProvider("anthropic", anthropic.Anthropic(api_key=settings.anthropic_api_key))


def _vertex_eu() -> AnthropicProvider:
    if not settings.vertex_project_id:
        raise LlmNotConfigured("VERTEX_PROJECT_ID mangler for LLM_BACKEND=vertex_eu")
    client = anthropic.AnthropicVertex(
        project_id=settings.vertex_project_id, region=settings.vertex_region
    )
    return AnthropicProvider("vertex_eu", client)


# ---- fake ------------------------------------------------------------------------------------


@dataclass
class FakeResponse:
    text: str
    stop_reason: str = "end_turn"
    model: str = "fake-model"
    usage: ProviderUsage = field(default_factory=lambda: ProviderUsage(1000, 200, 0, 0, "us"))


class FakeProvider:
    """Scripted responses, recorded requests. An empty script raises, so a test
    that forgot to script a call fails loudly instead of silently."""

    name = "fake"

    def __init__(self, responses: Sequence[FakeResponse | Exception] = ()) -> None:
        self.responses: list[FakeResponse | Exception] = list(responses)
        self.requests: list[ProviderRequest] = []

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        self.requests.append(req)
        if not self.responses:
            raise LlmNotConfigured("fake provider: intet scriptet svar (LLM_BACKEND=fake)")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return ProviderResponse(nxt.text, nxt.stop_reason, nxt.model, nxt.usage)


# ---- selection -------------------------------------------------------------------------------

_override: Provider | None = None


def set_provider(p: Provider | None) -> None:
    """Tests: inject a FakeProvider. None restores config-driven selection."""
    global _override
    _override = p


def current() -> Provider:
    if _override is not None:
        return _override
    backend = settings.llm_backend
    if backend == "anthropic":
        return _anthropic()
    if backend == "vertex_eu":
        return _vertex_eu()
    return FakeProvider()
