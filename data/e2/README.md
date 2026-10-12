# GPU 記憶體隔離的原始資料與摘要

`raw/` 保存九輪 MPS 與六輪 ConsumableShares 的必要證據。MPS 包含 4 GiB 與 8 GiB single-process 邊界，以及 8 GiB dual-process 情境各三輪；ConsumableShares 包含三輪容量記帳與三輪 8 GiB runtime 測試。原始輸入路徑列在 [SOURCES.md](SOURCES.md)，每個輸入的 SHA256 列在 [source-sha256.json](source-sha256.json)。

`summary.json` 與 `summary.csv` 包含全部輪次，`runtime.csv` 與 `accounting.csv` 分別保留兩種測試的列。摘要由 [共用分析程式](../../labs/04-isolation/common/analysis/analyze.py) 重新計算；`trial.json` 只保留參數、觀察時間與終止狀態，不帶入原報告已算好的 tensor 最大值、probe 次數或判定。

`mps-talk.csv` 是另外保存的歷史案例，取自吳磊（NVIDIA，2019-10-31）[原始投影片](https://www.nvidia.cn/content/dam/en-zz/zh_cn/assets/webinars/31oct2019c/20191031_MPS_davidwu.pdf)第 32 與 35 頁。它不參與 A40 隔離結果；ResNet-50 同時保留吞吐量整數 588／1538 與投影片所列 2.61 倍，整數相除約為 2.62，兩者差異在[書中案例](../../book/04-isolation/mps.md#哪些工作可能受惠)說明。

```sh
$ .venv/bin/python tools/extract_data.py e2
$ python3 labs/04-isolation/common/analysis/analyze.py data/e2/raw --output /tmp/isolation-summary
```

## 保留的證據

Runtime 輪次的 `task-a.log`、`task-b.log` 與 `gpu-during.txt` 保留原始 bytes。`gpu-metrics.jsonl` 只留下 B 活躍期間的取樣，以及時間、GPU UUID 與實體 VRAM 欄位；`claims-allocated.json`、事件與最終 Pod 快照則保留重算所需的欄位。Pod 快照保留 image digest、GPU Claim、節點與 container 終止狀態，移除網路位址與其他不需要的 API metadata。

記帳輪次保留釋放前後的 Claims、排程事件、A40 ResourceSlice 與實體 VRAM 快照。原始記帳測試在刪除 B 後才讀取其日誌，三輪 `task-b.log` 因 Pod NotFound 而沒有內容；記帳結果由 API 配置狀態與排程事件核對，摘要不把空日誌當作成功的 CUDA 證據。

## 重算方式

`b_tensor_gib_by_client` 是各 CUDA client 成功保留 tensor 的最大值，`b_max_tensor_gib` 是這些最大值的合計。MPS dual-process 測試另外要求兩個 client 的 `B_HOLD` 到 `B_HOLD_END` 有共同保留窗口；`hold_seconds` 與 `hold_probe_ok`、`hold_probe_oom` 只計算這個交集。`a_probe_ok_pressure` 與 `a_probe_oom_pressure` 計算整段 pressure phase，包含 B 增長 tensor 的時間，不能與 hold 欄位互換。

NVML 峰值的窗口由第一個 `B_GROW` 到第一個 `B_EXIT` 決定。原始取樣必須符合 `B_GROW.utc ≤ sample.utc` 且 `sample.finished_utc ≤ B_EXIT.utc`，讓整次查詢落在 B 活躍期間；`nvml_peak_used_mib` 取已用量最大值，`nvml_min_free_mib` 取可用量最小值。ConsumableShares 超過配額便同步退出，沒有 hold 窗口，因此 hold 欄位為空值；原始 `gpu-during.txt` 在 B 退出後取得，不能代替 B 活躍期間的取樣峰值。

容量記帳先從 A40 ResourceSlice 讀取公布容量，再加總已配置 Claim 的 `consumedCapacity.memory`，餘額為兩者之差。第三個 8 GiB Claim 的 Pending 與釋放後配置狀態直接讀取各輪的 Claim 快照，排程失敗訊息保留原文。NVML 的 free 是實體可用量，沒有扣除尚未建立 CUDA context 的 Claim 記帳量。

缺少 CUDA 身分、階段樣本、正常終止、共享 hold 標記、B 活躍期間的 NVML 取樣或清理驗證時，分析程式會標記 `complete=false`。觀察流程失敗或中斷也會保留不完整判定，不會因為後來收集到完整日誌而改成成功。CUDA 配置 OOM 與 Kubernetes `OOMKilled` 分別記錄；即使 B 正常處理配置 OOM 並以 exit code 0 結束，仍會保留 `allocation_oom` 結果。
