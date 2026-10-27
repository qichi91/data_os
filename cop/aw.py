#!/usr/bin/env python3
"""
agent_worker.py:
Coder Agent の CLI / ランタイムを呼び出してドキュメント変換タスクを実行させる。
LLMへの直接通信は行わず、Coder Agent にファイル操作・変換作業を一任する。
"""

import argparse
import os
import subprocess
import sys

ERROR_LOG_PATH = "tmp_validation_error.log"


def build_agent_prompt(sources: list[str], target: str, skill_path: str) -> str:
    """Coder Agent に渡す指示プロンプトを構築"""
    
    # 直前のバリデーションエラーがあればフィードバックとして注入
    error_context = ""
    if os.path.exists(ERROR_LOG_PATH):
        with open(ERROR_LOG_PATH, "r", encoding="utf-8") as f:
            err = f.read().strip()
        if err:
            error_context = (
                f"\n【前回のバリデーションエラー】\n"
                f"出力内容に以下の不備がありました。修正してください:\n"
                f"{err}\n"
            )

    sources_formatted = "\n".join([f"- {s}" for s in sources])

    prompt = f"""ドキュメント再構築タスクを実行してください。

【参照スキル定義】
{skill_path} を確認し、そのフォーマット規約と見出しルールに厳格に従ってください。

【入力ファイル】
以下のファイルの内容を読み取ってください:
{sources_formatted}

【出力先】
変換後の内容を以下のパスに書き込んでください:
- {target}
{error_context}
【完了条件】
- 指定された出力先に新しいファイルが正しく作成されていること。
"""
    return prompt


def main():
    parser = argparse.ArgumentParser(description="Coder Agent を使ったドキュメント変換ワーカー")
    parser.add_argument("--sources", required=True, help="入力ファイルパス（カンマ区切り）")
    parser.add_argument("--target", required=True, help="出力先ファイルパス")
    parser.add_argument("--skill", required=True, help="スキル定義ファイルパス")
    args = parser.parse_args()

    source_files = [s.strip() for s in args.sources.split(",") if s.strip()]
    prompt = build_agent_prompt(source_files, args.target, args.skill)

    print(f"  [Coder Agent] タスク投入開始: {args.target}")

    # Coder Agent を非対話（ヘッドレス/バッチ）モードで実行
    # ※ 環境内の Coder Agent CLI コマンドを指定（例: coder exp agent run や coder agent exec 等）
    cmd = [
        "coder", "exp", "agent", "run",
        "--prompt", prompt
    ]

    # もし Coder Workspace 内に coder-agent デーモン連携 CLI がある場合
    # (環境変数 CODER_AGENT_TOKEN はコンテナ内に自動注入されているため追加認証不要)
    try:
        res = subprocess.run(cmd, check=True, text=True, capture_output=True)
        print("  [Coder Agent] タスク完了")
        if res.stdout:
            print(f"  [Coder Agent 出力]:\n{res.stdout.strip()}")
            
        # 成功したら一時エラーログを削除
        if os.path.exists(ERROR_LOG_PATH):
            os.remove(ERROR_LOG_PATH)

    except subprocess.CalledProcessError as e:
        print(f"  [ERROR] Coder Agent の実行に失敗しました (Exit Code {e.returncode})", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
