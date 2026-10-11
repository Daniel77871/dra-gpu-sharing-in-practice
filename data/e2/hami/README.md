# HAMi 的容量記帳與執行期證據

`raw/` 保存三輪容量記帳、三輪單行程邊界與三輪同容器雙行程測試。[來源清單](SOURCES.md)列出實際讀取的原始檔案與行號範圍，[SHA256 清單](source-sha256.json)則辨識原始位元組。每個摘要都由[分析程式](../../../labs/04-isolation/common/analysis/hami.py)重算，未使用原始 `trial.json` 裡已計算的最大值或結論。

`summary.json` 與 `summary.csv` 保存全部輪次；`accounting.csv` 保存容量記帳，`runtime.csv` 保存 CUDA 測試。這些附加資料與[先前的 E2 資料](../README.md)分開存放，合併比較時必須保留兩種觀察窗口與 NVML 計量範圍。

```sh
$ .venv/bin/python tools/extract_data.py e2-hami
$ python3 labs/04-isolation/common/analysis/hami.py data/e2/hami/raw --output /tmp/hami-runtime-summary
```

## 保留與精簡方式

`task-a.log`、`task-b.log`、`gpu-during.txt`、`processes-during.txt`、`runtime-hooks.json` 與 `client-identity.json` 保留原始位元組。`client-processes.json` 保留行程身分、cgroup、命令與 libvgpu／Claim cache 的 maps 行，移除無關的函式庫映射。`gpu-metrics.jsonl` 保留整次查詢落在 B 活躍期間的實體 VRAM 取樣，以及 UTC、GPU UUID 與錯誤欄位。

Claims、A40 ResourceSlice、排程事件與終止快照只保留重算容量、GPU 身分、映像檔與容器退出所需欄位。網路位址與不相關的 API 中繼資料不匯出；原始命令封存與認證資料也不在此資料集內。

## 欄位定義

`b_max_tensor_gib` 是 B 各 CUDA client 成功保留張量最大值的合計；雙行程的共同 hold 窗口另由兩組 `B_HOLD`／`B_HOLD_END` 的交集驗證。`hold_probe_ok` 只計算該交集內 A 的新配置成功事件；`a_probe_ok_pressure` 涵蓋整段 pressure phase，包含 B 增長張量的時間。

`b_nvml_used_mib` 由 `client-identity.json` 的 host PID 對應到 `processes-during.txt`，加總 B 的實體行程快照。它不包含 A，也不代表每個瞬間的峰值；`nvml_peak_used_mib` 則是 B 活躍期間的整卡取樣峰值，包含 A 與其他開銷。兩者不可互換。

`reserved_mib`／`reserved_cores` 從已配置 Claim 的 `consumedCapacity` 加總，餘額由 A40 ResourceSlice 公布容量扣除。容量記帳測試每份 Claim 要求的 cores 與執行期不同，因此分別保存 `_before`／`_after` 與執行期欄位。cache 路徑相等與裝置／inode 相等分別記錄；缺少任一身分證據就不推定共享區相同。

與[書中的 HAMi 結果](../../../book/04-isolation/hami-dra.md#結果)及[跨方案比較](../../../book/04-isolation/results.md)對照時，應同時檢查 `complete` 與 `incomplete_reason`。CUDA 配置 OOM、Kubernetes `OOMKilled`、非零退出與清理缺漏是不同的事件。
