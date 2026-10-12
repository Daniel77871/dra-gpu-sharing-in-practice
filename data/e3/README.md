# 效能資料與精選原始批次

本目錄保存原 A40 固定工作量比較的 180 個有效批次、420 個 Task，以及每種模式一個完整 continuous 原始批次。書中的四張表由 `labs/05-performance/analysis/analyze.py` 重建；資料來源的實際讀取路徑見 [SOURCES.md](SOURCES.md)。

| 檔案 | 內容 |
|---|---|
| `benchmark-config.json`、`matrix.json` | 凍結的校準結果、固定工作量與原模式順序 |
| `summary.csv` | 每批指標，保留 block、Task 數、配對比值與恢復標記 |
| `group-statistics.csv`、`condition-statistics.json` | 原彙整指標及全部 Task 的延遲統計 |
| `task-timing.csv` | 獨立保存的 Pod／container UTC 時間與 GPU 工作時間 |
| `paired-mps-vs-timeslicing.csv` | 同一 block 內的吞吐量比值 |
| `reconciliation.json` | 逐批重建的數值、工作次數、有效性及恢復資訊；移除原絕對路徑 |
| `environment.json` | 從版本證據投影的 Kubernetes 版本、原生 driver image 摘要與 GPU driver 版本 |
| `report-tables.md` | 原報告三張工作表及一張模式比值表的精確值 |
| `raw/` | block 2 的 exclusive N=1，以及 sequential、TimeSlicing、MPS、HAMi N=2，各一個批次 |

原始批次包含每個 Task 的完整日誌、控制握手、host clock domain、數值驗證、終止狀態與逐列 NVML 取樣。Sequential 保留兩個 `serial-task-*` 子目錄，不把建立 Pod 的間隔加入服務時間。Kubernetes 終止狀態只保留分析必要欄位，移除 API annotation、Pod IP 與 container ID；Task 日誌、控制事件、時間戳及取樣列不改動。

這些精選原始批次用來驗證 per-batch 指標；完整表格仍由 180 列 summary 重建。重算彙整 P95 時，summary 的 `per_task_slowdown` 乘上同一 block 內 exclusive 的實際 Task 延遲，可還原全部 Task 延遲；`condition-statistics.json` 用來獨立核對彙整結果。不能將每批 P95 的平均當成表中的 P95。

原研究包含一次跨暫停的 continuous sequential N=4，以及兩次 MPS 的清理恢復。原失敗的 trial 檔保留在來源交付目錄，沒有改寫成成功，也沒有重跑 CUDA 工作；本目錄以 `continued_after_pause`／`cleanup_recovered` 記錄哪些正式數值沿用已核對的恢復證據。精選 raw 使用 block 2，不包含這些較大的恢復附件。

原量測的來源版本，與後續修正拒絕條件的分析版本分開保存；後者只重算既有日誌，不能宣稱它曾執行原 GPU 工作。這個版本差異記錄於來源交付的 `WORKFLOW_NOTES.md:30`，恢復批次的 SHA 與 exit code 記錄於 `RECOVERY_EVIDENCE.md:14`。圖表與表格呈現的是完整交付的最終資料，這些限制仍適用。

```sh
$ python3 labs/05-performance/analysis/analyze.py data/e3/summary.csv --output /tmp/performance-tables
$ python3 labs/05-performance/analysis/analyze.py data/e3/raw --output /tmp/performance-golden
```
