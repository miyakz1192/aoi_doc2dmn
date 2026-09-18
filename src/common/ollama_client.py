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
    max_retries: int = 2,
) -> dict:
    """OllamaにJSON形式での応答を強制して呼び出す。

    1回目はtemperature=0.0で呼び出す(再現性のため。reference/README.md方針に準拠)。
    format="json"制約下でも、生成が途中で打ち切られ不正なJSON(文字列が
    閉じていない等)が返ることがまれにあるため、その場合はmax_retries回まで
    再試行する。temperature=0.0は決定論的デコードのため、同じ入力を単純に
    再送しても同じ壊れた出力が返るだけで無意味(実際に3連続で同一の破損JSONが
    返る事例を観測済み)。そのためリトライ時のみ小さな非ゼロtemperatureを与え、
    デコード経路を変えて回復を狙う。
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        temperature = 0.0 if attempt == 0 else 0.3
        try:
            resp = requests.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": temperature},
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
            last_error = e
            if attempt < max_retries:
                continue
            raise OllamaCallError(
                f"LLM response is not valid JSON after {max_retries + 1} attempt(s): {raw!r}"
            ) from last_error
    raise AssertionError("unreachable")
