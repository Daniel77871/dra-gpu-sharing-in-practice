# 隔離實驗的共用介面

所有工作負載都由 [workload/kustomization.yaml](workload/kustomization.yaml) 產生 `isolation-code`，Namespace 為 `gpu-isolation-lab`。`base_workload.py` 提供 CUDA 配置、同步與 A 的持續服務，`isolation_workload.py` 提供單行程邊界和 MPS 的雙行程目標，`capacity_workload.py` 保留跨行程合計安全停止的容量方案版本；這三個檔案的 CUDA 邏輯與原實驗相同。記帳 sleeper 不建立 CUDA 張量。

## 每輪輸出

子實驗的 `run.sh` 設定 `BACKEND` 與 `LAB_DIR`，進入共用 controller。它先渲染 Kustomization，再將輸入分為 `base.json`、`claims.json` 與以角色命名的 Job／Claim JSON；套用前加上本次 `app.kubernetes.io/instance`。完整執行命令見[套件入口](../README.md)，目前提供 MPS 與 ConsumableShares 的 wrappers。

| 檔案或欄位 | 分析用途 |
|---|---|
| `trial.json` 的 `backend`、`case`、`quota_gib`、`repeat` | 識別方法、`accounting`／`boundary`／`scope` 條件與配額。 |
| `task-a.log`、`task-b.log` | A／B 的逐行 JSON 事件，保留 UTC、client ID 與成功張量量。 |
| `gpu-metrics.jsonl` 或 `gpu.csv` | 原始控制端查詢或 driver Pod 內連續取樣；CSV 可含 MiB 單位。 |
| `survivor-final.json`、`pressure-final.json` | 容器退出碼與 `OOMKilled`；不要只用 `B_EXIT` 取代容器實際終止。 |
| `baseline_elapsed`、`recovery_elapsed`、`b_actual_exit_utc`、`recovery_start_utc` | 核對觀察時間與 recovery 順序。 |
| `cleanup.verified` | 清理後的 GPU 與裝置狀態已核對。 |
| `claims-before-release.json`、`claims-after-release.json`、`resourceslices.json`、`events-before-release.json` | 記帳餘額、第三份 Claim 狀態轉變與 scheduler 事件。 |
| MPS `mps-control.json`、`mps-identity.json` | 生效的 server／default 配額與 client 到 Pod 的身分。 |

`analyze_round(path)` 回傳單列，`analyze_run(path)` 可讀一輪或一個執行目錄；`backend` 保留在輸出，不參與張量／時間窗口的演算法。命令列 `--output` 產生相同欄位的 JSON／CSV，退出碼 2 表示不完整或輸入錯誤；`--quiet` 只省略終端機判定輸出，摘要與退出碼相同。收集階段使用這個選項，runner 在每輪清理驗證後只印一次最終判定。新增方法時，保留上述檔案與欄位契約，就能重用 A／B 事件、共同 hold 與 NVML 窗口分析；provider 的 driver／pool／device 身分檢查則必須依該方法明確實作。

## 限制與清理

同一 Namespace 與 GPU 的輪次須依序執行。收集和清理會先核對 instance，再用已保存 Pod UID 核對日誌來源；舊 run-id 不會接管新工作。Job 與 Claim selector 同時限定 `part-of: dra-book`、component 與 instance，provider 操作保持在[獨立工具](../provider/README.md)。

手動 `cleanup.sh` 最後會刪除帶有 dra-book 標籤的 `gpu-isolation-lab` 並等待完成，前提是各類 Namespace 物件都屬於本次執行或 Kubernetes 預設物件；外來物件會阻止 Namespace 刪除。每輪自動清理保留 Namespace，讓下一輪重用；provider 與 `out/<run-id>/` 不受工作負載清理影響。

每輪開始與清理後都核對 A40 UUID、`Default`、沒有 compute 行程、記憶體用量最多 64 MiB，並等待該節點的 MPS daemon Pods／Deployment 消失。4090 的 MPS 物件不在這個等待範圍，工具也不會直接刪除 daemon；它由原生 driver 在 Claim 釋放後回收。中斷後的手動清理使用同樣條件。

MPS 的兩個 child 各自檢查配額，沒有 Pod 合計 guard；`capacity_workload.py` 的合計控制路徑不可被這個 MPS wrapper 替代。這個差異是實驗設計的一部分，將不同方法的 Claim 拓撲或安全規則改成相同，會改變待測問題。
