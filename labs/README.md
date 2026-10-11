# 復現實驗指南

這裡將提供各實驗章節的最小復現套件。先閱讀[書籍目錄](../book/README.md)及該實驗的 README，再確認環境與 GPU 閒置狀態；每個 lab 的 `VERIFIED.md` 記錄在原叢集上實際執行縮短版（smoke run）的結果。

## 前提

以下為原始實驗的環境前提；部署方式將在[第 2 章](../book/02-deploy/README.md)補齊。

| 元件 | 前提 |
| --- | --- |
| Kubernetes | v1.37.0，提供 DRA API `resource.k8s.io/v1` |
| GPU Operator | v26.7.0，NVIDIA driver 595.91.07，`devicePlugin.enabled=false` |
| NVIDIA DRA driver | 0.5.0，啟用 `MPSSupport` |
| 本機工具 | `kubectl`、`bash`；分析與共用檢查使用 `python3` 標準函式庫 |
| 權限 | 能讀取 Node、所有 Namespace 的 ResourceClaim、`gpu-operator` 的 Pod，並在 driver Pod 執行 `nvidia-smi`；實驗另需建立及清理其物件的權限 |

在可用 `kubectl` 存取叢集的主機執行，例如控制平面節點。GPU 查詢與取樣透過 `kubectl -n gpu-operator exec` 在目標節點的 driver Pod 中進行；Pod 依 `app=nvidia-driver-daemonset` 與節點篩選，容器為 `nvidia-driver-ctr`。

## 執行規則

- 一次只執行一個 lab，避免其他負載干擾量測。
- 每次先執行 GPU 閒置檢查；FAIL 時先查明占用來源。已配置且可在目標節點使用的 ResourceClaim 都會阻擋檢查，不限於本書的標籤；無法判定的狀態也回報 FAIL。[ResourceClaim API](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/) 定義 `status.allocation.nodeSelector` 的可用節點範圍。
- 閒置檢查預設要求每張 GPU 的 `memory.used < 100 MiB`，可用 `GPU_IDLE_MAX_MIB` 指定正數門檻。這是本套件的保守檢查規則，尚未以原始叢集驗證；即使 PASS，也要避免其他使用者在檢查後啟動負載。
- 執行結束或失敗後都要停止取樣並執行該 lab 的 `cleanup.sh`；清理只刪除帶有 `app.kubernetes.io/part-of=dra-book` 的物件。
- **切換 DRA provider 等改變叢集共用狀態的腳本僅限實驗叢集。** 後續套件必須將這類操作獨立成腳本，提供備份、驗證與還原步驟；還原時檢查原始 DaemonSet 的 UID 與 spec 是否與備份一致。

## 輸出與模式

每次執行以 UTC 時間及本機 PID 產生 run id，所有紀錄寫入 `labs/<lab>/out/<run-id>/`；可重用指定 run id 以追加命令紀錄。`commands.log` 保存實際 kubectl 命令及結束碼，其他檔案由各 lab 定義，包含 Pod 日誌、物件快照及 GPU 取樣 CSV。`out/` 已排除於版本控制，公開結果前仍須確認沒有私密資料。

完整模式保留原始實驗參數。`SMOKE=1` 使用縮短參數，用來快速確認整個流程可以跑通；縮短方式由各 lab 的 README 說明，不能把 smoke 結果當作書中的完整量測結果。

## 標準目錄

```text
labs/<lab>/
  README.md
  manifests/            # 完整 YAML，含由 workload/ 產生的 ConfigMap
  workload/             # 在固定映像檔內執行的 Python 工作負載
  scripts/run.sh
  scripts/collect.sh
  scripts/cleanup.sh
  analysis/             # Python 標準函式庫，輸出與書中表格相同的欄位
  VERIFIED.md           # 在原叢集實際執行縮短版後的驗證紀錄
  out/<run-id>/         # 不納入版本控制
```

## 命名與對照

Namespace 採 `gpu-<lab>-lab`，物件以角色命名，例如 `steady-worker`、`pressure-worker` 或 `shared-gpu`。所有物件加上 `app.kubernetes.io/part-of: dra-book` 與 `app.kubernetes.io/component: <role>`；批次工作用 Job，各 Pod 獨立配置時用 ResourceClaimTemplate，共用同一配置時用 ResourceClaim。各章會列出「原始物件名稱 → 本書名稱」對照表，保留原始映像檔、環境變數、命令、selector、GpuConfig 與 Claim 共用方式。

## 共用工具

以下示範共用工具的介面，`dra-a40` 為可替換的目標節點；這不是實際執行紀錄。先載入 `lib.sh` 並以 `init_run <lab-dir> [run-id]` 建立輸出目錄，`OUT_DIR` 會傳給後續工具。

```bash
$ source labs/common/lib.sh
$ init_run labs/common
$ bash labs/common/gpu-idle-check.sh dra-a40
$ bash labs/common/gpu-sample.sh start dra-a40 "$OUT_DIR/gpu.csv"
# 在這裡執行所選 lab 的 run.sh 與 collect.sh
$ bash labs/common/gpu-sample.sh stop dra-a40 "$OUT_DIR/gpu.csv"
# 最後執行所選 lab 的 cleanup.sh
```

取樣每 500 ms 記錄時間、GPU UUID、名稱、VRAM 總量與使用量及 GPU 使用率。`<out-csv>.pid` 保存本機取樣行程的 PID、節點、輸出路徑及隨機識別碼；停止時透過同一識別碼要求管理行程結束它自己建立的子行程，收到確認才移除 PID 檔，重複停止可安全執行。啟動時沿用同一份 `OUT_DIR/commands.log`；未設定 `OUT_DIR` 的獨立取樣會將命令紀錄放在 CSV 旁。既有 CSV 不會被覆寫，取樣錯誤寫入 `<out-csv>.stderr.log`。

後續 lab 可使用 `wait_pod_phase <namespace> <name> <phase> [timeout秒]`、`wait_job_complete <namespace> <name> [timeout秒]`、`wait_claim_allocated <namespace> <name> [timeout秒]`。Job 失敗會提早回報，等待超時也會失敗；三者的預設等待上限分別為 300、600、300 秒。`kc` 的一般 API 查詢預設有 25 秒 request timeout，連續取樣則關閉該 timeout。
