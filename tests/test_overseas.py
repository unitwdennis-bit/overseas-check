"""境外背調 CLI 的單元測試。不叫真的 claude；測試對象一律合成資料。"""
import json
import subprocess

import pytest

import cli
import overseas

PERSON = {"kind": "person", "name_cn": "陳大文", "name_en": "CHAN TAI MAN", "birth_year": "1970",
          "employer_company": "測試貿易有限公司", "region_hint": "HK"}
COMPANY = {"kind": "company", "name_en": "TEST HOLDINGS LIMITED", "registration_no": "12345678",
           "region_hint": "SG"}


def test_person_prompt_has_subject_sites_and_date():
    prompt = overseas.build_prompt(PERSON, "2026-09-28")
    assert "姓名（全部排列）：CHAN TAI MAN／TAI MAN CHAN／陳大文" in prompt
    assert "出生年：1970" in prompt and "地区线索：香港" in prompt
    assert "本次检索日期：2026-09-28" in prompt
    assert "elitigation.sg" in prompt and "opensanctions.org" in prompt
    assert "{" not in prompt, "佔位符沒換乾淨"
    assert "PEP 必须判定" in prompt


def test_prompt_lists_only_filled_fields():
    prompt = overseas.build_prompt({"kind": "person", "name_en": "TEST"}, "2026-09-28")
    assert "出生年" not in prompt.split("【查询对象】")[1].split("按以下")[0]
    assert "地区线索" not in prompt.split("【查询对象】")[1]


def test_company_prompt():
    prompt = overseas.build_prompt(COMPANY, "2026-09-28")
    assert "公司名称：TEST HOLDINGS LIMITED" in prompt and "注册号：12345678" in prompt
    assert "地区线索：新加坡" in prompt and "七、检索方法附录" in prompt


def test_claude_cmd_isolates_global_claude_md_and_limits_tools():
    cmd = overseas.claude_cmd({"claude_bin": "claude.exe"})
    assert cmd[cmd.index("--setting-sources") + 1] == "local"
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[-2:] == ["--disallowedTools", "Bash"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"


def test_strip_preamble():
    assert overseas.strip_preamble("全部搜索完成。\n\n---\n# 报告\n内容").startswith("# 报告")
    assert overseas.strip_preamble("现在我有充分的信息。\n\n---\n\n## 报告\n内容").startswith("## 报告")
    assert overseas.strip_preamble("没有标题") == "没有标题"


def _claude_json(result, **kw):
    return json.dumps({"is_error": False, "result": result, "num_turns": 10,
                       "total_cost_usd": 1.5, **kw}, ensure_ascii=False)


def test_parse_claude():
    ok = overseas.parse_claude(_claude_json("# 报告\n〇、"), "", 0)
    assert ok.ok and ok.turns == 10 and ok.cost == 1.5
    assert not overseas.parse_claude("不是 JSON", "boom", 1).ok
    assert not overseas.parse_claude(_claude_json("x", is_error=True), "", 1).ok
    assert not overseas.parse_claude(_claude_json(""), "", 0).ok


FULL_PERSON = "# 报告\n" + "\n".join(f"{s}节" for s in overseas.SECTIONS["person"])


def test_run_combines_reports_and_flags_missing_sections(tmp_path):
    texts = iter([FULL_PERSON, "# 公司报告\n〇、一、二、"])
    result = overseas.run([PERSON, COMPANY], tmp_path, {},
                          one=lambda s, c, t: overseas.OneResult(True, next(texts), 5, 1.0))
    assert result.status == "done" and result.turns == 10
    body = (tmp_path / "overseas.md").read_text(encoding="utf-8")
    assert "# 报告" in body and "# 公司报告" in body and "\n---\n" in body
    assert result.detail.startswith("⚠️ 報告不完整：第2位缺三、四、五、六、七節")
    assert "陳大文" not in result.detail and "TEST" not in result.detail


def test_run_partial_failure_still_done(tmp_path):
    outcomes = iter([overseas.OneResult(True, FULL_PERSON, 5, 1.0),
                     overseas.OneResult(False, error="逾時（2400 秒）", timeout=True)])
    result = overseas.run([PERSON, PERSON], tmp_path, {}, one=lambda s, c, t: next(outcomes))
    assert result.status == "done" and "第2位失敗：逾時" in result.detail
    assert "查询失败" in (tmp_path / "overseas.md").read_text(encoding="utf-8")


def test_run_all_timeout(tmp_path):
    result = overseas.run([PERSON], tmp_path, {},
                          one=lambda s, c, t: overseas.OneResult(False, error="逾時", timeout=True))
    assert result.status == "timeout" and result.reply_path == ""
    assert not (tmp_path / "overseas.md").exists()


def test_run_one_sends_prompt_by_stdin_not_argv(monkeypatch):
    seen = {}

    class FakeProc:
        pid = 1
        returncode = 0

        def communicate(self, prompt=None, timeout=None):
            seen["prompt"] = prompt
            return _claude_json(FULL_PERSON), ""

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(overseas.subprocess, "Popen", fake_popen)
    r = overseas.run_one(PERSON, {}, "2026-09-28")
    assert r.ok
    assert "陳大文" in seen["prompt"]
    assert not any("陳大文" in part for part in seen["cmd"])


def test_run_one_timeout_kills_tree(monkeypatch):
    killed = []

    class SlowProc:
        pid = 4321
        returncode = None
        calls = 0

        def communicate(self, prompt=None, timeout=None):
            SlowProc.calls += 1
            if SlowProc.calls == 1:
                raise subprocess.TimeoutExpired("claude", timeout)
            return "", ""

    monkeypatch.setattr(overseas.subprocess, "Popen", lambda cmd, **kw: SlowProc())
    monkeypatch.setattr(overseas.subprocess, "run", lambda cmd, **kw: killed.append(cmd))
    r = overseas.run_one(PERSON, {"timeout_seconds": 1})
    assert r.timeout and not r.ok
    assert killed and killed[0][:5] == ["taskkill", "/PID", "4321", "/T", "/F"]


def test_cli_json_contract_and_exit_codes(tmp_path, monkeypatch, capsys):
    subject_file = tmp_path / "s.txt"
    subject_file.write_text(json.dumps({"subjects": [PERSON]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(overseas, "run_one", lambda s, c, t=None: overseas.OneResult(True, FULL_PERSON, 7, 2.0))
    code = cli.main(["check", "--subject-file", str(subject_file), "--out-dir", str(tmp_path / "out"), "--json"])
    out = json.loads(capsys.readouterr().out.strip())
    assert code == 0 and out["status"] == "done" and out["turns"] == 7
    assert out["reply_path"].endswith("overseas.md")

    code = cli.main(["check", "--subject-file", str(tmp_path / "沒有這個檔"), "--out-dir", str(tmp_path), "--json"])
    assert code == 3 and json.loads(capsys.readouterr().out.strip())["status"] == "failed"


def test_cli_bad_args_exit_2():
    with pytest.raises(SystemExit) as exc:
        cli.main(["check"])
    assert exc.value.code == 2
