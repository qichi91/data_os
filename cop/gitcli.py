#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

# --- 設定項目（環境変数または直接指定） ---
GITEA_URL = os.getenv("GITEA_URL", "https://gitea.example.com").rstrip("/")
GITEA_TOKEN = os.getenv("GITEA_TOKEN", "your_personal_access_token")
REPO_OWNER = os.getenv("REPO_OWNER", "my-org")
REPO_NAME = os.getenv("REPO_NAME", "my-repo")
TARGET_LABEL = "status:todo"
DONE_LABEL = "status:review-ready"
MAX_ISSUES_PER_RUN = 5  # 1回の夜間実行で処理する最大Issue数


# --- Gitea API ヘルパー ---
def gitea_api(endpoint: str, method: str = "GET", data: dict | None = None) -> dict | list:
    url = f"{GITEA_URL}/api/v1{endpoint}"
    headers = {
        "Authorization": f"token {GITEA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:  # No Content
            return {}
        return json.loads(resp.read().decode("utf-8"))


def run_command(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """シェルコマンド実行のラッパー"""
    print(f"  [EXEC] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


# --- Issue 本文のメタデータ解析 ---
def parse_issue_body(body: str) -> dict:
    """
    Issue 本文から入力ファイルと出力先パスを抽出
    想定フォーマット:
      ### 入力ドキュメント
      - docs/old/a.md
      - docs/old/b.md
      ### 出力先
      - docs/new/a.md
    """
    sources = []
    targets = []
    
    current_section = None
    for line in body.splitlines():
        line = line.strip()
        if "入力ドキュメント" in line:
            current_section = "source"
            continue
        elif "出力先" in line:
            current_section = "target"
            continue
        elif line.startswith("###"):
            current_section = None
            continue
        
        if line.startswith("-") or line.startswith("*"):
            path = line.lstrip("-* ").strip()
            if current_section == "source":
                sources.append(path)
            elif current_section == "target":
                targets.append(path)

    return {"sources": sources, "target": targets[0] if targets else None}


# --- エージェント実行・自己修復ループ ---
def execute_agent_migration(sources: list[str], target: str) -> bool:
    """
    エージェントを呼び出して変換し、バリデータで検証。
    失敗時はエラーログをフィードバックして再試行（自己修復ループ）。
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"  -> 変換処理の実行 (試行 {attempt}/{max_retries})...")
        
        # 1. エージェント/CLIの実行（プロジェクトの構成に合わせて呼び出し）
        # ※ Coder Agent の CLI や、所定のエージェントスクリプトをコール
        agent_cmd = [
            "python3", "scripts/agent_worker.py",
            "--sources", ",".join(sources),
            "--target", target,
            "--skill", ".coder/agent/skills/doc_migration.md"
        ]
        run_command(agent_cmd, check=True)

        # 2. 決定論的バリデーション（フォーマット・構文検査）
        print("  -> バリデーション実行中...")
        val_res = run_command(["python3", "scripts/validate_doc.py", target], check=False)
        
        if val_res.returncode == 0:
            print("  -> バリデーション合格！")
            return True
        else:
            print(f"  [警告] バリデーションエラー発生:\n{val_res.stderr}")
            # エラー内容をファイル等に出力し、次のループでエージェントに読ませる
            with open("tmp_validation_error.log", "w") as f:
                f.write(val_res.stderr)

    return False


# --- メイン処理 ---
def main():
    print("=== 夜間ドキュメント変換バッチ開始 ===")

    # 1. 対象ラベルの付いたOpenなIssueを取得
    issues = gitea_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues?state=open&labels={TARGET_LABEL}")
    if not issues:
        print("処理対象のIssueはありませんでした。")
        return

    processed_count = 0
    for issue in issues:
        if processed_count >= MAX_ISSUES_PER_RUN:
            print(f"本日の上限件数 ({MAX_ISSUES_PER_RUN}件) に達したため終了します。")
            break

        issue_id = issue["number"]
        issue_title = issue["title"]
        print(f"\n--- Processing Issue #{issue_id}: {issue_title} ---")

        # 2. 本文パース
        params = parse_issue_body(issue.get("body", ""))
        sources = params["sources"]
        target = params["target"]

        if not sources or not target:
            print(f"  [SKIP] 入力または出力先のパースに失敗しました (Issue #{issue_id})")
            continue

        branch_name = f"docs/issue-{issue_id}"
        
        try:
            # 3. Git ブランチ準備
            run_command(["git", "checkout", "main"])
            run_command(["git", "pull", "origin", "main"])
            run_command(["git", "checkout", "-B", branch_name])

            # 4. 変換 & バリデーション実行
            success = execute_agent_migration(sources, target)
            if not success:
                print(f"  [ERROR] リトライ上限に達したためスキップします (Issue #{issue_id})")
                continue

            # 5. Git Commit & Push
            run_command(["git", "add", target])
            commit_msg = f"docs: migrate documents for Issue #{issue_id}\n\nRefs: #{issue_id}"
            run_command(["git", "commit", "-m", commit_msg])
            run_command(["git", "push", "-u", "origin", branch_name, "--force"])

            # 6. Gitea Pull Request 作成
            pr_data = {
                "base": "main",
                "head": branch_name,
                "title": f"[自動変換] {issue_title} (Fixes #{issue_id})",
                "body": (
                    f"Issue #{issue_id} に基づくドキュメント自動再構築PRです。\n\n"
                    f"**変換元:**\n" + "\n".join([f"- `{s}`" for s in sources]) + "\n\n"
                    f"**出力先:**\n- `{target}`\n\n"
                    f"バリデーションチェック通過済み。"
                )
            }
            pr_res = gitea_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls", method="POST", data=pr_data)
            pr_url = pr_res.get("html_url", "")
            print(f"  -> PR作成完了: {pr_url}")

            # 7. Issue のラベル更新とコメント追加
            comment_data = {"body": f"ドキュメントの自動変換が完了しました。PRを作成しました: {pr_url}"}
            gitea_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_id}/comments", method="POST", data=comment_data)

            # ラベル付け替え (todo削除 -> review-ready追加)
            gitea_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_id}/labels", method="PUT", data={"labels": [DONE_LABEL]})

            processed_count += 1

        except Exception as e:
            print(f"  [EXCEPTION] 処理中にエラーが発生しました: {e}")
        finally:
            run_command(["git", "checkout", "main"])

    print(f"\n=== 全処理終了 (完了: {processed_count}件) ===")


if __name__ == "__main__":
    main()
