"""Ollamaへの共通呼び出しヘルパー。

stage1_filter(LLMフォールバック)・stage2_classify(軸1・軸2分類)の両方から
利用する。reference/gijinka-extraction-pipeline/src/pipeline/classifiers/
local_llm_strategy.py の call_ollama_json() を移植したもの。
"""
from __future__ import annotations

import json

import requests

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b-instruct"


class OllamaCallError(Exception):
    pass


def call_ollama_json(
    prompt: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout: int = 300,
) -> dict:
    """OllamaにJSON形式での応答を強制して呼び出す。

    temperature=0.0で呼び出す(再現性のため。reference/README.md方針に準拠)。
    """
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaCallError(f"failed to call ollama at {base_url} (model={model}): {e}") from e
    raw = resp.json().get("response", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaCallError(f"LLM response is not valid JSON: {raw!r}") from e
