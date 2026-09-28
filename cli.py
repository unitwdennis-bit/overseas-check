"""境外背調 CLI。介面比照 doubao-check，CMS 用 subprocess 呼叫：

    uv run cli.py check --subject-file <檔> --out-dir <夾> --json

stdout 一行 JSON：{"status","detail","turns","cost_usd","reply_path"}；進度與錯誤走 stderr。
退出碼：0 done、2 參數用錯、3 failed、5 逾時。
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

import overseas

EXIT = {"done": 0, "failed": 3, "timeout": 5}


def load_config(path: Path = Path(__file__).resolve().parent / "config.yaml") -> dict:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="境外背調（公開網路檢索）")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--subject-file", required=True, help="{\"subjects\": [...]} 的 JSON 檔")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--json", action="store_true", help="stdout 印一行 JSON 摘要")
    args = parser.parse_args(argv)

    try:
        subjects = json.loads(Path(args.subject_file).read_text(encoding="utf-8")).get("subjects")
    except (OSError, ValueError, AttributeError) as exc:
        subjects, err = None, type(exc).__name__
    else:
        err = "沒有查詢對象"
    if not isinstance(subjects, list) or not subjects:
        result = overseas.Result("failed", f"查詢對象檔讀不到（{err}）", 0, 0.0, "")
    else:
        print(f"境外背調：{len(subjects)} 位，逐位查詢中…", file=sys.stderr)
        result = overseas.run([s for s in subjects if isinstance(s, dict)],
                              Path(args.out_dir), load_config())

    summary = {"status": result.status, "detail": result.detail, "turns": result.turns,
               "cost_usd": result.cost_usd, "reply_path": result.reply_path}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"{result.status}｜{result.detail}｜{result.reply_path}", file=sys.stderr)
    return EXIT.get(result.status, 3)


if __name__ == "__main__":
    sys.exit(main())
