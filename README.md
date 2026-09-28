# overseas-check — 開戶前的境外背調（公開網路檢索）

給沒有中國身分證的人（含港澳）與境外公司，用 `claude -p` 只開 WebSearch／WebFetch 做一趟
公開來源背景核查，報告（簡體中文）合成一份 `overseas.md`。CMS（cms_box）的「境外背調」按鈕呼叫它。

**這份報告是線索，不是結論**：命中要回原始來源核實，「未檢索到」不代表沒有紀錄。開戶後的
正式查核是 cli-pilot 的 foreign check（CMS 上叫「境外核查」），它拿這份報告當線索逐條回官方來源確認。

## 用法

```bash
uv sync
cp config.example.yaml config.yaml    # 填 claude.exe 的絕對路徑
uv run cli.py check --subject-file subjects.json --out-dir <資料夾> --json
```

查詢對象檔：`{"subjects": [{...}, ...]}`，每一位帶 `kind`（`person`／`company`），欄位由 CMS 的
`app/overseas.py` `query_subject` 組好：

| kind | 欄位 |
|---|---|
| person | name_cn、name_en、former_name、birth_year、employer_company、region_hint |
| company | name_cn、name_en、registration_no、registered_address、established、region_hint |

人**不送**證件號、完整生日、地址（公開網路沒有拿證件號索引的來源，送了是純風險）；公司的登記資料本來就公開。

stdout 一行 JSON `{"status","detail","turns","cost_usd","reply_path"}`，退出碼：0 done、2 參數用錯、
3 failed、5 逾時。`detail` 只放行為事實（turns、費用、第幾位缺哪幾節或失敗原因），**不含姓名**——
CMS 會把它寫進狀態檔與 log、發 TG。

## 怎麼跑的

- **一位一趟** `claude -p`，提示詞走 stdin（姓名不進命令列）。至少一位成功就算 done，失敗的那位在報告裡留一段「查询失败」，detail 標 `⚠️ 報告不完整：第N位失敗：…`
- **不分國家**：提示詞是一份統一清單（`overseas/prompts/sites.md`：港、新、加官方站＋三份全球名單），地區由模型依資料自己判定並寫在報告開頭；CMS 看得出來時多給一個 `region_hint`
- **章節檢查**：人 〇～五、公司 〇～七，少了就在 detail 標「報告不完整」，報告照交
- 🔴 **`--setting-sources local` 不能拿掉**：沒加的話 claude 會讀到使用者全域 CLAUDE.md（規定一律繁體），2026-09-28 實測報告自己改成繁體還附註「依本機全域設定」，而且時有時無
- 逾時（config `timeout_seconds`，預設 2400 秒）用 `taskkill /T /F` 連子行程一起殺

## 模型與成本（2026-09-28 原型，Opus 5）

| 對象 | 時間 | 費用 |
|---|---|---|
| 人（給地區） | 11 分 | $3.43 |
| 人（統一清單、不給地區） | 17.5 分 | $3.94 |
| 公司 | 14.6 分 | $3.67 |
| 人（加拿大，正式 CLI 實跑） | 16.7 分 | $3.17 |

四個模型比較過（許仕仁陽性對照）：Sonnet 5 漏 PEP；Opus 4.6 便宜一半但少了判決書系統與案號；
使用者選 Opus 5。

已知限制：UK 制裁名單、ICIJ 這類要瀏覽器或有機器人驗證的站，聯網搜尋常常進不去，報告會照實寫「未執行」；
正式的三份全球名單查核由開戶後的境外核查（foreign check，用瀏覽器）負責。ChatGPT 網頁版不採用：複製會把引用網址全拿掉，合規留底缺一截。

## 測試

```bash
uv run pytest -q
```

全部不叫真的 claude：`tests/conftest.py` 會讓任何沒換掉的 `subprocess.Popen` 直接失敗。
測試對象一律合成資料。
