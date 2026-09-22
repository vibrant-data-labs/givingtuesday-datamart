"""The retry policy and the parser in ``vlm_transcription``, with no model in the loop."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from givingtuesday_datamart import vlm_transcription as vlm


class _Client:
    """Answers one scripted (text, finish_reason) per call and records whether JSON mode was asked for."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.json_modes: list[bool] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.json_modes.append("response_format" in kwargs)
        text, finish = self.answers.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=len(text)))


def _answer(rows, kind="grants_paid_list", truncated=False):
    body = json.dumps({"page_kind": kind, "heading": "", "rows": rows, "totals": []})
    return body[: body.rindex("}") - 20] if truncated else body    # stops inside the last row


def _rows(n):
    return [{"name": f"Recipient Number {i}", "address": "", "status": "PC", "purpose": "", "amount": 100} for i in range(n)]


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "p001.png"
    path.write_bytes(b"\x89PNG")
    return path


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(vlm.time, "sleep", lambda seconds: None)


def test_an_empty_list_page_is_asked_again_without_json_mode(png):
    client = _Client([(_answer([]), "stop"), (_answer(_rows(5)), "stop")])
    data = vlm.transcribe(client, "google/gemini-3.5-flash-lite", png)
    assert client.json_modes == [True, False]
    assert len(data["rows"]) == 5 and data["_attempts"] == 2
    assert data["_json_mode"] is False and data["_prompt"] == vlm.PROMPT_VERSION


def test_qwen_is_never_asked_in_json_mode(png):
    client = _Client([(_answer(_rows(2)), "stop")])
    data = vlm.transcribe(client, "alibaba/qwen3-vl-instruct", png)
    assert client.json_modes == [False] and data["_attempts"] == 1


def test_a_page_that_is_not_a_list_is_taken_at_its_word(png):
    client = _Client([(_answer([], kind="other"), "stop")])
    data = vlm.transcribe(client, "google/gemini-3.5-flash-lite", png)
    assert data["rows"] == [] and data["_attempts"] == 1 and client.json_modes == [True]


def test_the_fullest_answer_is_kept_when_the_retries_are_worse(png):
    client = _Client([(_answer(_rows(4), truncated=True), "stop"), (_answer([]), "stop"), (_answer([]), "stop")])
    data = vlm.transcribe(client, "alibaba/qwen3-vl-instruct", png)
    assert data["_partial"] and len(data["rows"]) == 3 and data["_attempts"] == 3


def test_amounts_written_as_printed_still_parse():
    text = '{"page_kind": "grants_paid_list", "heading": "", "rows": [{"name": "Alpha Trust", "amount": $151,000.00}], "totals": []}'
    data = vlm._parse(text)
    assert "parse_error" not in data and data["rows"][0]["amount"] == "151,000.00"
