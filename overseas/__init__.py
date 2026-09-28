"""境外背調：一位一趟 `claude -p` 聯網搜尋，報告合成一份 overseas.md。

查詢對象檔 `{"subjects": [{kind, name_cn, name_en, ...}, ...]}` 由 CMS 寫好（見 cms_box app/overseas.py
的 query_subject）；這一層只負責組提示詞、叫 claude、檢查章節、合併報告。
"""
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
REGION_LABEL = {"HK": "香港", "MO": "澳门", "SG": "新加坡", "CA": "加拿大"}

# 章節標記：提示詞要求的節名開頭。少了就在 detail 標「報告不完整」，報告照樣交
SECTIONS = {
    "person": ["〇、", "一、", "二、", "三、", "四、", "五、"],
    "company": ["〇、", "一、", "二、", "三、", "四、", "五、", "六、", "七、"],
}


def kind_of(subject: dict) -> str:
    return "company" if subject.get("kind") == "company" else "person"


def name_variants(name_en: str) -> list[str]:
    """英文名的兩種排列：原樣、第一個字移到最後（姓在前／名在前兩種都要查）。"""
    tokens = name_en.split()
    variants = [name_en.strip()] if name_en.strip() else []
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[1:] + tokens[:1]))
    return variants


def subject_block(subject: dict) -> str:
    """【查询对象】那一段。只列有值的欄位——列出「未提供」等於留空格給模型猜。"""
    hint = REGION_LABEL.get(subject.get("region_hint", ""), "")
    if kind_of(subject) == "company":
        names = [n for n in (subject.get("name_en"), subject.get("name_cn")) if n]
        rows = [("公司名称", "／".join(names)),
                ("注册号", subject.get("registration_no")),
                ("注册地址", subject.get("registered_address")),
                ("成立日期", subject.get("established")),
                ("地区线索", hint)]
    else:
        names = name_variants(subject.get("name_en") or "") + [subject.get("name_cn") or ""]
        rows = [("姓名（全部排列）", "／".join(n for n in names if n)),
                ("曾用名", subject.get("former_name")),
                ("出生年", subject.get("birth_year")),
                ("已知关联公司", subject.get("employer_company")),
                ("地区线索", hint)]
    return "\n".join(f"{label}：{value}" for label, value in rows if value)


def build_prompt(subject: dict, today: str | None = None) -> str:
    template = (PROMPTS / f"{kind_of(subject)}.md").read_text(encoding="utf-8")
    sites = (PROMPTS / "sites.md").read_text(encoding="utf-8").rstrip("\n")
    return (template.replace("{today}", today or date.today().isoformat())
            .replace("{sites}", sites)
            .replace("{subject_block}", subject_block(subject)))


def missing_sections(kind: str, text: str) -> list[str]:
    return [m.rstrip("、") for m in SECTIONS[kind] if m not in text]


def strip_preamble(text: str) -> str:
    """提示詞要求從標題開始，但模型偶爾先講一句「全部搜索完成…」（09-28 Opus 4.6、Haiku 實測）。
    第一個 Markdown 標題（任何層級，Haiku 用的是 `##`）之前的東西拿掉；沒有標題就原樣回。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"#{1,6} ", line):
            return "\n".join(lines[i:]).strip() + "\n"
    return text


@dataclass
class OneResult:
    ok: bool
    text: str = ""
    turns: int = 0
    cost: float = 0.0
    error: str = ""          # 只放行為事實，不含姓名
    timeout: bool = False


def claude_cmd(config: dict) -> list[str]:
    # --setting-sources local：不讀使用者全域 CLAUDE.md。09-28 實測沒加時報告會照全域規定
    # 改用繁體（還附註「依本機全域設定」），跟提示詞的簡體打架，而且時有時無
    # --disallowedTools 吃不定個數的參數，一定要放最後
    return [config.get("claude_bin") or "claude", "-p", "--output-format", "json",
            "--model", config.get("model") or "claude-opus-5",
            "--setting-sources", "local",
            "--allowedTools", "WebSearch,WebFetch",
            "--disallowedTools", "Bash"]


def run_one(subject: dict, config: dict, today: str | None = None) -> OneResult:
    """查一位。提示詞走 stdin（姓名不進命令列）。逾時連同子行程一起殺掉。"""
    timeout = float(config.get("timeout_seconds") or 2400)
    try:
        proc = subprocess.Popen(claude_cmd(config), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    except OSError as exc:
        return OneResult(False, error=f"叫不動 claude（{type(exc).__name__}）")
    try:
        out, err = proc.communicate(build_prompt(subject, today), timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.communicate()
        return OneResult(False, error=f"逾時（{int(timeout)} 秒）", timeout=True)
    return parse_claude(out, err, proc.returncode)


def parse_claude(out: str, err: str, code: int) -> OneResult:
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return OneResult(False, error=f"claude 輸出不是 JSON（退出碼 {code}）：{(err or '')[-200:]}")
    turns, cost = data.get("num_turns", 0) or 0, data.get("total_cost_usd", 0.0) or 0.0
    if data.get("is_error") or code != 0:
        return OneResult(False, turns=turns, cost=cost, error=f"claude 回報錯誤（{data.get('subtype', code)}）")
    text = strip_preamble(data.get("result") or "")
    if not text.strip():
        return OneResult(False, turns=turns, cost=cost, error="claude 沒有回覆內容")
    return OneResult(True, text, turns, cost)


@dataclass
class Result:
    status: str              # done／failed／timeout
    detail: str
    turns: int
    cost_usd: float
    reply_path: str


def run(subjects: list[dict], out_dir: Path, config: dict, today: str | None = None,
        one=None) -> Result:
    """逐位查完，合成 <out_dir>/overseas.md。至少一位成功就算 done，其餘寫進 detail。

    `one` 預設在呼叫當下才取 run_one——寫成 `one=run_one` 會在 def 當下綁死，測試換掉
    run_one 也不生效，09-28 就這樣在單元測試裡真的叫起了 claude。
    """
    one = one or run_one
    results = [one(s, config, today) for s in subjects]
    turns = sum(r.turns for r in results)
    cost = sum(r.cost for r in results)
    problems = []
    parts = []
    for n, (subject, r) in enumerate(zip(subjects, results), 1):
        if r.ok:
            missing = missing_sections(kind_of(subject), r.text)
            if missing:
                problems.append(f"第{n}位缺{'、'.join(missing)}節")
            parts.append(r.text.strip())
        else:
            problems.append(f"第{n}位失敗：{r.error}")
            name = subject.get("name_cn") or subject.get("name_en") or f"第{n}位"
            parts.append(f"# {name}：查询失败\n\n{r.error}")
    cost_note = f"{turns} turns / ${cost:.4f}"
    detail = f"⚠️ 報告不完整：{'；'.join(problems)}｜{cost_note}" if problems else cost_note
    if not any(r.ok for r in results):
        status = "timeout" if results and all(r.timeout for r in results) else "failed"
        return Result(status, detail, turns, cost, "")
    out_dir.mkdir(parents=True, exist_ok=True)
    reply = out_dir / "overseas.md"
    reply.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
    return Result("done", detail, turns, cost, str(reply))
