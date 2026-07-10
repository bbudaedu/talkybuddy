# -*- coding: utf-8 -*-
"""CloudLLM（Bedrock Converse 陪聊 provider）單元測試；boto3 全 mock。"""
import types
import pytest
from server import cloud_llm, config


class _FakeScaffold:
    def __init__(self, target):
        self.target_sentence = target


def _fake_converse_response(text):
    return {"output": {"message": {"content": [{"text": text}]}}}


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.last_kwargs = None

    def converse(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture(autouse=True)
def _force_bedrock(monkeypatch):
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(config, "COMPANION_MODEL_ID", "test-model", raising=False)


def test_generate_returns_filtered_text(monkeypatch):
    fake = _FakeClient(_fake_converse_response("你好棒！跟我說一遍：I see a cat"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    out = cloud_llm.CloudLLM().generate("我看到貓", _FakeScaffold("I see a cat"))
    assert out == "你好棒！跟我說一遍：I see a cat"
    # 送出的 modelId 來自 config、且學生文字有進 prompt
    assert fake.last_kwargs["modelId"] == "test-model"


def test_generate_appends_target_when_missing(monkeypatch):
    fake = _FakeClient(_fake_converse_response("你今天很棒喔！"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    out = cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("I see a dog"))
    assert "跟我說一遍：I see a dog" in out


def test_generate_none_on_exception(monkeypatch):
    fake = _FakeClient(raises=RuntimeError("boom"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    assert cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("hi")) is None


def test_generate_none_when_guardrail_blocks(monkeypatch):
    fake = _FakeClient(_fake_converse_response("我們來聊殺人的東西"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    assert cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("hi")) is None


def test_generate_deidentifies_student_text(monkeypatch):
    fake = _FakeClient(_fake_converse_response("很好！"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    cloud_llm.CloudLLM().generate("我的電話號碼是1234", _FakeScaffold("hi"))
    sent = fake.last_kwargs["messages"][0]["content"][0]["text"]
    assert "電話號碼" not in sent or "1234" not in sent  # deidentify 已遮罩


def test_available_false_when_provider_off(monkeypatch):
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "off", raising=False)
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: object())
    assert cloud_llm.CloudLLM().available() is False


def test_available_false_when_no_client(monkeypatch):
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: None)
    assert cloud_llm.CloudLLM().available() is False


def test_available_true_when_bedrock_and_client(monkeypatch):
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: object())
    assert cloud_llm.CloudLLM().available() is True
