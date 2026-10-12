# 共用 GPU 的 OOM 重現套件

本套件讓 `steady-service`（A）與 `greedy-batch`（B）共用 `a40-shared` ResourceClaim。A 保留 1 GiB tensor 做原地運算，並 probe 新的 512 MiB 配置；B 以 256 MiB 區塊耗盡 VRAM。目的在於區分既有運算、新配置與 container 存活，完整機制與原始六輪結果見[第 3 章](../../book/03-oom/README.md)。

## 前置條件

先完成[第 2 章的 DRA 部署](../../book/02-deploy/README.md)。原始環境是 Kubernetes v1.37.0、NVIDIA DRA v0.5.0、NVIDIA driver 595.91.07，workload 使用 PyTorch 2.5.1+cu124；image 固定為 `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`。

- 控制端需要 Bash、Python 3 與含 Kustomize 的 `kubectl`。本機分析只使用 Python 標準 library，CUDA 與 PyTorch 在 container 內執行。
- 叢集必須只公布一顆符合 `productName == "NVIDIA A40"` 的 GPU，且在 `GPU_NODE` 上，預設為 `dra-a40`。這是保留原 CEL selector 並讓取樣目標確定的前置檢查；多顆 A40 時會停止。
- `gpu.nvidia.com` DeviceClass 不得附加裝置設定，GPU 的 compute mode 必須為 `Default`。每輪先確認沒有已配置到目標節點的 Claim、CUDA process，且 GPU 用量低於共用閒置檢查的門檻；`GPU_IDLE_MAX_MIB` 預設 100 MiB，是操作政策而非實測基準。
- GPU Operator 的 driver Pod 必須在目標節點 Ready。取樣經 `kubectl -n gpu-operator exec` 進入 `nvidia-driver-ctr`，不需要登入 worker。
- 權限須涵蓋本 Namespace 的 Job、Pod、ConfigMap、ResourceClaim、events、Pod logs 與 exec，以及唯讀的 Node、ResourceSlice、DeviceClass、全叢集 ResourceClaim 與 driver Pod 清單。清理還需要列出所有可列舉的 Namespace 內資源並刪除本實驗 Namespace，以便檢查其他物件是否屬於套件。

**此實驗會刻意耗盡整顆 GPU 的可配置 VRAM。請使用閒置 GPU。** 腳本遇到既有負載或不屬於 `dra-book` 的同名物件就停止，保留事件與命令紀錄供檢查。

## 檔案與角色

| 路徑或名稱 | 用途 |
|---|---|
| `workload/workload.py` | 原始 single-process CUDA workload，保留 `survivor`／`pressure` 角色及全部事件名稱 |
| `manifests/kustomization.yaml` | 完整 Namespace、Claim、Job 與 workload ConfigMap generator |
| `steady-service` | A 的 Job；名稱描述持續服務的角色，本實驗仍是批次 Job |
| `greedy-batch` | B 的 Job；描述持續占用 VRAM 的批次工作 |
| `analysis/analyze.py` | 重算 CSV、JSON 與和書中相同的 Markdown 表格 |
| `out/<run-id>/` | 本機保存資料，已由 repository 規則忽略 |

ConfigMap generator 從相鄰 `workload/` 讀取程式，因此渲染時需要 `--load-restrictor=LoadRestrictionsNone`。`run.sh` 會保存完整渲染結果，再依階段分別套用 Namespace／ConfigMap、Claim、A、B；直接一次套用整套 YAML 會略過基準與控制握手。

```sh
$ cd labs/03-oom
$ kubectl kustomize manifests --load-restrictor=LoadRestrictionsNone > /tmp/gpu-oom-manifests.yaml
```

## 執行

完整模式依序執行 exit、hold，各三輪。可指定 run-id 以便後續收集；省略時使用 UTC 時間與本機 process ID。

```sh
$ bash scripts/run.sh original-parameters
```

縮短版保留 tensor 大小、成長速度、基準 60 秒、hold 30 秒與恢復觀察 60 秒，只縮減為 hold 一輪。這樣仍能看見壓力期間的新配置失敗，不會因縮短 hold 而失去 probe 樣本。

```sh
$ SMOKE=1 bash scripts/run.sh smoke-a40
```

| 參數 | 意義 | 預設或縮短版行為 |
|---|---|---|
| `MODE` | `both`、`exit` 或 `hold` | 預設 `both`；`SMOKE=1` 固定 `hold` |
| `ROUNDS` | 每種模式的輪數 | 預設 3；`SMOKE=1` 固定 1 |
| `SMOKE` | 縮短版開關 | 預設 0；1 保留原時間參數 |
| `GPU_NODE` | 閒置檢查與 driver Pod 取樣節點 | 預設 `dra-a40`；可填自己的 A40 節點名稱 |

例如只做 exit 兩輪：

```sh
$ MODE=exit ROUNDS=2 GPU_NODE=dra-a40 bash scripts/run.sh exit-only
```

每輪依序做閒置檢查、啟動 A、等待 `A_READY`、觀察基準，然後送出關閉 probe 的控制命令並等待 `CONTROL_ACK`。取樣啟動後才建立 B，核對 A／B 的 GPU UUID 和 Claim 的兩個 `reservedFor`，最後才讓 B 成長。Hold 模式在 `B_HOLD` 後重新啟用 A probe；B 終止後再觀察 A，送出正常停止命令、保存證據，刪除 Job 並等待 Pod 消失，接著刪除 Claim 以便下一輪重新檢查閒置狀態。

B 發生預期 CUDA OOM 後回傳 exit code 1，所以 `greedy-batch` Job 的 Failed 是本實驗的預期狀態。腳本核對 `B_OOM`、`B_EXIT`、exit code 與 reason，不以 Job Complete 判定 B 的結果；`backoffLimit: 0` 和 `restartPolicy: Never` 防止重跑。

## 資料收集與分析

每輪的主要資料如下，檔名沿用原始證據格式：

```text
out/<run-id>/
  run.json, target.json, manifests.yaml, version.json
  summary.csv, summary.json, SUMMARY.md
  hold-r1/
    a.jsonl, b.jsonl, gpu.jsonl
    phases.json, trial.json, claim.json, events.json
    survivor-final.json, pressure-final.json
    commands.log
```

`gpu.jsonl` 保留 `utc`、`gpu_uuid`、`gpu_name`、`total_mib`、`used_mib`、`free_mib`、`utilization_pct`、`compute_mode`。新執行以 `nvidia-smi --query-gpu=... --format=csv,noheader,nounits -lms 500` 連續取樣，`utc` 是控制端收到該列的 UTC 時間。原正式資料則由逐次 kubectl 呼叫取樣，每次呼叫後等待 1 秒；兩者的取樣密度不同，分析不依賴相同列數。

若執行中斷，先重新收集仍存在的 Pod，再清理。`collect.sh` 核對 Pod UID；Pod 已刪除時保留先前日誌，已完成清理的輪次不重寫快照。中斷的觀察期不能補成完整實驗，分析會回傳 2 並列出缺少的證據。

```sh
$ bash scripts/collect.sh smoke-a40
$ python3 analysis/analyze.py out/smoke-a40
```

成功執行後，同一 run-id 再執行只重做離線分析。中斷的 run-id 會拒絕重啟；收集、清理後使用新的 run-id，避免混合不同輪次。

原始資料也能完全離線重算，輸出另存於 `out/`：

```sh
$ python3 analysis/analyze.py ../../data/e1/raw --output out/original-analysis
```

原始 hold 三輪的新配置 OOM 為 30、30、29 次，OK 都為 0；六輪 A 運算錯誤都是 0，A／B exit code 分別為 0／1。自己的叢集未必出現相同 tensor 門檻或 probe 次數；先確認 `complete` 為 True，再對照[章節結果](../../book/03-oom/README.md#結果)與 `incomplete_reason`。Exit 模式沒有 hold 窗口，欄位留空表示未測。

分析會拒絕把 RAM `OOMKilled`、GPU UUID 不一致、缺少 `B_OOM`、不完整觀察期、缺少正常停止事件或取樣失敗當成完成的 CUDA OOM 實驗。恢復秒數從較晚的 `B_EXIT.utc`／CRI `finishedAt` 算到首次 `A_PROBE_OK`，包含 probe 及控制延遲，不能視為 driver 釋放耗時。

## 清理

先收集再清理；清理會移除帶 `app.kubernetes.io/part-of=dra-book` 且符合本實驗 component 的 Job、Pod、Claim、ConfigMap，再刪除同樣帶所屬 label 的 `gpu-oom-lab` Namespace，並等待刪除完成。刪除 Namespace 前會檢查所有可列舉的資源，容許系統建立的 `default` ServiceAccount、`kube-root-ca.crt` 與事件；若留下其他沒有套件 label 的物件，就停止並保留 Namespace，本機 `out/` 仍供後續分析。

```sh
$ bash scripts/cleanup.sh smoke-a40
```

清理可重複執行。腳本同時停止該 run-id 的本機取樣器，停止要求帶取樣器 nonce，不會把檔案中的任意 PID 當作可終止的 process。

## 實機驗證紀錄

2026-10-11 在原叢集執行 `smoke-20261011` 縮短版，B 在 OOM 時保留 43,776 MiB，hold 期間 A 的新配置為 30 次 OOM、0 次 OK。B 退出後首次配置成功為 0.847 秒，hold p95 倍率為 0.994，詳見[實機驗證紀錄](VERIFIED.md)。

該輪 apply 的命令紀錄顯示以下四個步驟都以 0 結束；命令紀錄未保存標準輸出，以下節錄各步驟的退出行。Namespace 與產生的 ConfigMap 一起套用，其他物件按基準、成長與 hold 的控制順序建立。

```sh
$ kubectl apply -f out/smoke-20261011/base.json
2026-10-11T12:38:46Z pid=444405 exit=0
$ kubectl apply -f out/smoke-20261011/claim-manifest.json
2026-10-11T12:38:47Z pid=444418 exit=0
$ kubectl apply -f out/smoke-20261011/steady-service-manifest.json
2026-10-11T12:38:47Z pid=444431 exit=0
$ kubectl apply -f out/smoke-20261011/hold-r1/greedy-batch-manifest.json
2026-10-11T12:39:51Z pid=444693 exit=0
```
