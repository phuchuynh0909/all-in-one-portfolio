"""Structured-output method selection and parse-error recovery.

Offline: mutates the in-process capability table and a fake runnable. No LLM
calls. Covers the provider split (Ollama keeps ``json_schema``, openai_compatible
defaults to ``function_calling``) and the recovery wrapper that turns a
``.parse()`` ValidationError into ``None``.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.services.tradingagents import runner


@pytest.fixture
def _caps_isolation():
    from tradingagents.llm_clients import capabilities as caps_mod

    snapshot = dict(caps_mod._BY_ID)
    yield caps_mod
    caps_mod._BY_ID.clear()
    caps_mod._BY_ID.update(snapshot)


def _clear_structured_env(monkeypatch) -> None:
    for name in (
        "TRADINGAGENTS_STRUCTURED_METHOD",
        "TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD",
        "TRADINGAGENTS_OPENAI_COMPATIBLE_STRUCTURED_METHOD",
    ):
        monkeypatch.delenv(name, raising=False)


def _cfg(*roles: tuple[str, str, str]) -> dict:
    return {
        "llm_roles": {
            name: {"provider": provider, "model": model}
            for name, provider, model in roles
        }
    }


def test_openai_compatible_defaults_to_function_calling(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    runner.apply_structured_method(
        _cfg(("deep", "openai_compatible", "proxy-qwen-manager"))
    )
    assert (
        _caps_isolation.get_capabilities("proxy-qwen-manager").preferred_structured_method
        == "function_calling"
    )


def test_ollama_defaults_to_json_schema(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    runner.apply_structured_method(_cfg(("quick", "ollama", "llama-test-analyst")))
    assert (
        _caps_isolation.get_capabilities("llama-test-analyst").preferred_structured_method
        == "json_schema"
    )


def test_mixed_run_splits_local_providers(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    runner.apply_structured_method(
        _cfg(
            ("deep", "openai_compatible", "proxy-qwen-manager"),
            ("quick", "ollama", "llama-test-analyst"),
        )
    )
    assert (
        _caps_isolation.get_capabilities("proxy-qwen-manager").preferred_structured_method
        == "function_calling"
    )
    assert (
        _caps_isolation.get_capabilities("llama-test-analyst").preferred_structured_method
        == "json_schema"
    )


def test_ollama_env_does_not_steer_openai_compatible(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD", "json_mode")
    runner.apply_structured_method(
        _cfg(
            ("deep", "openai_compatible", "proxy-qwen-manager"),
            ("quick", "ollama", "llama-test-analyst"),
        )
    )
    assert (
        _caps_isolation.get_capabilities("proxy-qwen-manager").preferred_structured_method
        == "function_calling"
    )
    assert (
        _caps_isolation.get_capabilities("llama-test-analyst").preferred_structured_method
        == "json_mode"
    )


def test_compat_env_overrides_openai_compatible_only(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_OPENAI_COMPATIBLE_STRUCTURED_METHOD", "json_schema")
    runner.apply_structured_method(
        _cfg(
            ("deep", "openai_compatible", "proxy-qwen-manager"),
            ("quick", "ollama", "llama-test-analyst"),
        )
    )
    assert (
        _caps_isolation.get_capabilities("proxy-qwen-manager").preferred_structured_method
        == "json_schema"
    )
    assert (
        _caps_isolation.get_capabilities("llama-test-analyst").preferred_structured_method
        == "json_schema"
    )


def test_global_override_wins_for_both_local_providers(monkeypatch, _caps_isolation):
    _clear_structured_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_STRUCTURED_METHOD", "json_mode")
    monkeypatch.setenv("TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD", "json_schema")
    monkeypatch.setenv("TRADINGAGENTS_OPENAI_COMPATIBLE_STRUCTURED_METHOD", "function_calling")
    runner.apply_structured_method(
        _cfg(
            ("deep", "openai_compatible", "proxy-qwen-manager"),
            ("quick", "ollama", "llama-test-analyst"),
        )
    )
    assert (
        _caps_isolation.get_capabilities("proxy-qwen-manager").preferred_structured_method
        == "json_mode"
    )
    assert (
        _caps_isolation.get_capabilities("llama-test-analyst").preferred_structured_method
        == "json_mode"
    )


def test_is_structured_parse_error_accepts_validation_and_json():
    class _Decision(BaseModel):
        signal: str

    try:
        _Decision.model_validate_json("**Phán quyết danh mục**")
    except ValidationError as exc:
        assert runner._is_structured_parse_error(exc)

    assert runner._is_structured_parse_error(
        json.JSONDecodeError("Expecting value", "x", 0)
    )
    assert runner._is_structured_parse_error(
        ValueError("Invalid JSON: expected value at line 1 column 1")
    )
    assert not runner._is_structured_parse_error(RuntimeError("connection reset"))
    assert not runner._is_structured_parse_error(ValueError("rate limited"))


def test_recovery_wrapper_turns_parse_error_into_none():
    class _Boom:
        def invoke(self, *_a, **_k):
            raise ValueError("Invalid JSON: expected value at line 1 column 1")

    wrapped = runner._RecoveringStructuredRunnable(_Boom(), "proxy-qwen-manager")
    assert wrapped.invoke("prompt") is None


def test_recovery_wrapper_reraises_unrelated_errors():
    class _Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("429 rate limited")

    wrapped = runner._RecoveringStructuredRunnable(_Boom(), "proxy-qwen-manager")
    with pytest.raises(RuntimeError, match="429"):
        wrapped.invoke("prompt")


def test_patch_structured_output_recovery_wraps_bound_runnable(monkeypatch):
    from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

    class _Boom:
        def invoke(self, *_a, **_k):
            raise ValueError("Invalid JSON: expected value at line 1 column 1")

    class _FakeLLM:
        model_name = "proxy-qwen-manager"

    monkeypatch.setattr(runner, "_structured_output_patched", False)
    monkeypatch.setattr(
        NormalizedChatOpenAI,
        "with_structured_output",
        lambda self, schema, *, method=None, **kwargs: _Boom(),
    )

    runner.patch_structured_output_recovery()

    bound = NormalizedChatOpenAI.with_structured_output(_FakeLLM(), object)
    assert isinstance(bound, runner._RecoveringStructuredRunnable)
    assert bound.invoke("prompt") is None
