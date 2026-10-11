# E4 資料

這份資料保留各情境的原始 trial 狀態與離線驗證結果，並包含未通過、blocked 與 unsupported 的紀錄。`acceptance.json` 是原始報告的核對表；`trials.json` 保留可重新彙總的情境、輪次、CUDA 與 Claim 回收證據，`task-results.json` 保留 Task 的配置位置與時間。

`capacity-samples.json` 保留 DRA 與 NVML 的配對值、取樣時間差和 API 讀取區間。兩者不是同一瞬間的原子快照；exclusive allocation 的 remaining 保持 null，不轉換為零。範圍表的差值方向為 DRA remaining 減 Physical free。

從完整儲存庫執行分析：

```bash
$ python3 labs/06-heterogeneous/analysis/analyze.py --e4 data/e4 --e4b data/e4b
```

[SOURCES.md](SOURCES.md) 列出擷取器實際讀取的來源，對應原始內容的 SHA256、行數與位元組數在 [source-hashes.json](source-hashes.json)。此處只保留分析需要的欄位，不包含完整 API inventory 或命令紀錄。

詳見[異質 GPU 實驗結果](../../book/06-heterogeneous/experiments.md)。
