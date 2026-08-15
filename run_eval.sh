#!/usr/bin/env bash
# eval/run_eval.py の起動スクリプト。
#
# 使い方:
#   ./run_eval.sh              # --variant both で実行(デフォルト、30分程度)
#   ./run_eval.sh --variant raw
#   ./run_eval.sh --variant filled
#   ./run_eval.sh check        # Ollama疎通確認のみ(実行しない)
#
# 事前条件:
#   - Ollamaが起動していて、qwen2.5:14b-instructがロード済みであること
#     (未起動なら 'ollama serve'、モデル未取得なら 'ollama pull qwen2.5:14b-instruct')
#   - Python 3 (venvモジュール込み)。.venv/が無ければ自動作成しrequirements.txtを導入する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
MODEL="${OLLAMA_MODEL:-qwen2.5:14b-instruct}"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python3"

echo "== aoi_doc2dmn 評価ハーネス起動チェック =="

if [ ! -x "$PYTHON" ]; then
  echo "初回セットアップ: $VENV_DIR が無いため作成します..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --upgrade pip -q
  "$VENV_DIR/bin/pip" install -r requirements.txt -q
  echo "OK: 仮想環境を作成し依存関係をインストールしました"
else
  echo "OK: 仮想環境 ($VENV_DIR) を使用します"
fi

TAGS_JSON="$(curl -s -m 3 "${OLLAMA_URL}/api/tags" || true)"
if [ -z "$TAGS_JSON" ]; then
  echo "エラー: Ollama (${OLLAMA_URL}) に接続できません。" >&2
  echo "  対処: 別ターミナルで 'ollama serve' を実行してから再試行してください。" >&2
  exit 1
fi

if ! echo "$TAGS_JSON" | grep -q "\"${MODEL}\""; then
  echo "エラー: モデル '${MODEL}' が見つかりません。" >&2
  echo "  対処: 'ollama pull ${MODEL}' を実行してください。" >&2
  exit 1
fi
echo "OK: Ollama稼働中、モデル ${MODEL} 利用可能"
echo

if [ "${1:-}" = "check" ]; then
  echo "疎通確認のみ完了(実行はしていません)。"
  exit 0
fi

if [ $# -eq 0 ]; then
  set -- --variant both
  echo "(引数未指定のため --variant both で実行します。LLM呼び出し約50回・CPU推論のため30分程度かかります)"
fi

echo "実行: $PYTHON -m eval.run_eval $*"
echo
exec "$PYTHON" -m eval.run_eval "$@"
