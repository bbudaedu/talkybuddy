# -*- coding: utf-8 -*-
"""Anthropic 相容 Messages API 的端點/認證/model 解析（純函式、只讀 env、不觸網）。

diagnose 與 cloud_llm 共用同一組環境變數，支援官方金鑰或自架中轉（relay）。
認證順序與官方 SDK 一致：ANTHROPIC_AUTH_TOKEN（→ Authorization: Bearer）優先於
ANTHROPIC_API_KEY（→ x-api-key）。端點由 ANTHROPIC_BASE_URL 覆蓋（自架中轉）；
model 由 ANTHROPIC_DEFAULT_OPUS_MODEL 或 ANTHROPIC_MODEL 覆蓋。金鑰只讀 env、絕不寫進 repo。
"""

from __future__ import annotations

import os

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_VERSION = "2023-06-01"


def messages_url(base: str) -> str:
    """把 ANTHROPIC_BASE_URL（root、含 /v1、或完整端點）正規化成 Messages API 端點。

    相容官方 SDK base_url 慣例（root → 補 /v1/messages）。
    """
    base = base.strip().rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def resolve_config() -> dict | None:
    """由環境變數解析雲端腦呼叫設定；回 {"url","model","headers"} 或無憑證時 None。"""
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not (auth_token or api_key):
        return None

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    else:
        headers["x-api-key"] = api_key

    base = os.environ.get("ANTHROPIC_BASE_URL")
    url = messages_url(base) if base else DEFAULT_URL
    model = (
        os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or DEFAULT_MODEL
    )
    return {"url": url, "model": model, "headers": headers}
