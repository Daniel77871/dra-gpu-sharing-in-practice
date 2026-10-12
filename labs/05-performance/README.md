# 共用 GPU 的排程與效能重現套件

本套件在同一顆 A40 上比較 exclusive、sequential、TimeSlicing、MPS 與 HAMi，量測固定工作量的 Task 吞吐量、延遲與 GPU 使用率。Workload 與原正式執行相同，差別在於將 Pod 改為不重試的 Job、使用角色名稱，並透過 Pod 內的 emptyDir 傳遞控制命令。機制、完整設定與原始結果見[第 5 章](../../book/05-performance/README.md)。

## 前置條件

本套件必須從本儲存庫的完整 clone 執行，保留 `labs/` 與 `data/` 的相對路徑。Runner 會讀取 `data/e3/benchmark-config.json` 的固定工作量，以及 `data/e3/matrix.json` 的批次順序；同步到執行端時須一併保留這些資料。

先完成[第 2 章的部署](../../book/02-deploy/README.md)。原始環境是 Kubernetes v1.37.0、NVIDIA DRA v0.5.0、NVIDIA driver 595.91.07；workload 固定為 `docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`。控制端需要 Bash、Python 3 與含 Kustomize 的 `kubectl`；控制與分析程式只使用 Python 標準 library，CUDA 與 PyTorch 在 container 內執行。

- 目標 GPU 必須閒置：沒有 CUDA compute process、沒有配置到該裝置的 Claim、VRAM 使用量不超過 64 MiB、compute mode 為 `Default`。這是沿用原始 runner 的進入條件，並非通用 GPU 閒置定義。
- 目標 UUID 必須只由一個 ResourceSlice publisher 在指定節點公布。NVIDIA 模式須啟用 `MPSSupport=true,TimeSlicingSettings=true`，關閉 `ConsumableShares`，且 `allowMultipleAllocations` 關閉；`gpu.nvidia.com` DeviceClass 不得附加覆寫設定。這些沿用原正式 provider 的參數，須先執行下面的獨立設定步驟；benchmark runner 不改寫 provider。
- GPU Operator driver Pod 的 `nvidia-driver-ctr` 必須 Ready，主機必須有 `/usr/bin/python3` 及 GPU Operator 的 `/run/nvidia/driver`。取樣器由 `kubectl -n gpu-operator exec` 進入該 Pod，再用主機 chroot 的原生 NVML，每 500 ms 取樣；不需登入 worker 節點。
- 操作者需要本 Namespace 的 Job、Pod、ConfigMap、ResourceClaim、ResourceClaimTemplate、events、logs、exec 與刪除權限，以及唯讀的全叢集 ResourceClaim、ResourceSlice、DeviceClass 和 GPU Operator Pod 清單。最後清理 Namespace 還需要 API 資源探索、列出該 Namespace 所有資源及刪除 Namespace 的權限。MPS client 與 server 使用相同 root UID，`multiUser` 為 false。

**完整 HAMi 比較需要切換目標節點的 DRA provider，會改變節點上的 GPU 供應方式。** benchmark runner 只檢查當下 provider；相依的 [HAMi-DRA provider 套件](../04-isolation/hami-dra/README.md)已提供 [install-provider.sh](../04-isolation/hami-dra/scripts/install-provider.sh) 與 [uninstall-provider.sh](../04-isolation/hami-dra/scripts/uninstall-provider.sh)，建立 `hami-core-gpu.project-hami.io` DeviceClass，並在還原時核對原 DaemonSet UID 與 spec。未安裝時先執行四種 NVIDIA 模式。**原生分享設定與 HAMi 切換不能疊用：** `install-native-settings.sh` 執行期間原生 DaemonSet 維持 `OnDelete`，HAMi 安裝腳本會把它視為已被其他流程暫停而拒絕安裝；請先 `restore-native-settings.sh`，再安裝 HAMi provider，HAMi 批次完成並解除安裝後，若還有 NVIDIA 批次，再重新安裝原生分享設定續跑。

HAMi 使用每個 Task 的獨立 Claim，要求 `8Gi` 與 cores `100/N`；預設 DeviceClass 名稱為 `hami-core-gpu.project-hami.io`，可用 `HAMI_DEVICE_CLASS` 指定相依套件建立的名稱。Runner 核對每個 client 的 `CUDA_DEVICE_MEMORY_LIMIT_0=8192m`、`CUDA_DEVICE_SM_LIMIT_0`、獨立 cache inode、已載入的 library 及原始 `libvgpu.so` SHA256 `56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f`。供應套件若採用其他 library 版本，會停止，不會把它混入原版本的比較。

## 準備與還原原生分享設定

**這些獨立腳本會修改既有 NVIDIA DaemonSet 的 Pod 範本與更新策略，並重新建立目標節點的 plugin Pod。只在專用實驗叢集、GPU 閒置時執行；先保存備份，結束後核對還原。** 第二章的原始安裝只明確開啟 MPS，本步驟額外啟用 TimeSlicingSettings，避免把未套用 timeslice 政策的執行當成原正式條件。

從本套件目錄執行，設定用的 run-id 與 benchmark run-id 分開：

```sh
$ cd labs/05-performance
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash scripts/install-native-settings.sh native-performance
$ bash scripts/verify-native-settings.sh native-performance
```

備份位於 `out/native-performance/native-provider/`，保留原 DaemonSet JSON／YAML、UID、spec、原 Pod 與其他節點 plugin UID。安裝保留其餘功能開關與原 image，只將三個效能開關設為 `MPSSupport=true,TimeSlicingSettings=true,ConsumableShares=false`，暫時用 `OnDelete` 更新策略，僅刪除目標節點的舊 plugin Pod。每次 patch 都以 JSON Patch 測試原 UID 與當前 spec；Pod 刪除帶 UID／resourceVersion 前置條件，其他節點 plugin UID 改變便停止。

正式工作全部收集、清理，並且 HAMi 已解除、原生 provider 回復後，再還原：

```sh
$ bash scripts/restore-native-settings.sh native-performance
```

還原先核對原 DaemonSet UID 與本次設定 spec，在 `OnDelete` 下恢復原範本，只重新建立目標 plugin，最後恢復原更新策略並逐欄核對原 UID／spec；結果保存在 `restored.json`。驗證腳本只有查詢；備份、安裝及還原可重入，但已還原的設定 run-id 不可再用於新的安裝。腳本支援 `GPU_NODE`／`GPU_UUID`，以及 `NVIDIA_DRA_NAMESPACE`／`NVIDIA_DRA_DAEMONSET`；後兩者預設為 `nvidia-dra-driver-gpu`／`dra-driver-nvidia-gpu-kubelet-plugin`，只支援以 `FEATURE_GATES` 環境變數設定的原生 provider。

混合 HAMi 矩陣的 provider 安裝必須備份**這個已準備好的原生狀態**；HAMi 的解除安裝先回到此狀態，矩陣才可繼續。所有 provider 邊界完成後，才使用上述還原命令回到第二章原本的 DaemonSet。

## 執行完整模式

保持在套件目錄，使用新的 run-id 保存新資料：

```sh
$ bash scripts/run.sh full-native
```

預設 `MODES=all` 執行四種 NVIDIA 模式，共 135 個批次；`HAMI=1` 加入 HAMi 後才是原正式矩陣的 180 個批次。兩者都沿用三種工作、N=1/2/4、五個 block 及原矩陣的輪替與隨機順序。Exclusive 固定 N=1，sequential 只有 N=2/4；指定 N=2 或 N=4 時，仍納入 exclusive N=1 作為延遲與單 Task 的參照。

```sh
$ HAMI=1 bash scripts/run.sh full-with-hami
```

混合 provider 的矩陣會在空閒批次邊界停下，exit code 為 78，並寫入 `out/full-with-hami/provider-required.json`，其中 `required_driver` 指出下一批需要 NVIDIA 或 HAMi。完成上述相依套件的明確安裝／還原操作後，以**相同參數及 run-id** 重複命令；已完成批次不重跑，直到 `completed.json` 出現。原順序包含多次 provider 邊界，不能同時讓兩個 provider 公布同一顆 GPU。

也可以在 HAMi provider 已安裝時單獨執行：

```sh
$ MODES=hami HAMI=1 bash scripts/run.sh full-hami
```

HAMi 單獨執行缺少同 block 基準，Speedup 與 paired ratio 會留空；它不能直接取代完整配對矩陣。以下例子縮減比較範圍，但維持正式工作量：

```sh
$ MODES="exclusive sequential timeslicing mps" WORKLOADS=bursty-staggered NS="2 4" BLOCKS=2 bash scripts/run.sh staggered-subset
```

| 參數 | 意義 | 預設或縮短版行為 |
|---|---|---|
| `MODES` | 空白分隔的模式，或 `all` | `all` 選四種 NVIDIA 模式；`HAMI=1` 再加入 HAMi |
| `WORKLOADS` | 工作種類 | `continuous bursty-aligned bursty-staggered` |
| `NS` | 每批 Task 數，空白分隔 | `1 2 4`；exclusive 一律為 1 |
| `BLOCKS` | 使用原矩陣前幾個 block | 5，可選 1 至 5 |
| `SMOKE` | 縮短版 | 0；1 固定 aligned、N=2、四種 NVIDIA 模式、一個 block |
| `HAMI` | 加入 HAMi | 0；1 加入 HAMi，包含縮短版 |
| `GPU_NODE` | 節點 | `dra-a40` |
| `GPU_UUID` | 精確選取的 GPU | `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af` |
| `HAMI_DEVICE_CLASS` | HAMi 的 DeviceClass | `hami-core-gpu.project-hami.io` |

在自己的 A40 節點執行時，將節點名稱與 UUID 同時換掉。腳本會將它們寫入 workload 設定、CEL selector 與取樣目標：

```sh
$ GPU_NODE=<a40-node> GPU_UUID=<gpu-uuid> SMOKE=1 bash scripts/run.sh smoke-own-a40
```

## 執行縮短版

```sh
$ SMOKE=1 bash scripts/run.sh smoke-a40
```

縮短版使用 bursty-aligned，包含 exclusive N=1、sequential N=2、TimeSlicing N=2、MPS N=2，各一個 block。GEMM 次數按約十分之一縮減為 `ceil(367/10)=37`；仍保留 8192² FP32、TF32 關閉、種子、每次 73,705,924 ns 等待與十次 warmup。總計算次數及**總等待量**都縮減為 `37/367`，因此計算與等待比例保持相同；若再縮短每次等待，比例反而會改變。

目標是在 image 可用、GPU 閒置的環境中約十分鐘內完成四個批次，實際耗時須由實機驗證確認。縮短版只有一個 block，且 Task 很短，適合檢查控制握手、配額接線、資料完整性與清理；不能用它重現五個 block 的正式數字。

## 控制流程與部署檔案

`manifests/base/` 包含 Namespace、Job 與 ConfigMap generator；`manifests/overlays/` 分別保留五種 Claim 設定。Sequential 使用 ResourceClaimTemplate 逐個建立、釋放獨占 Claim；HAMi 使用同一種範本機制讓同批 Task 各自取得容量 Claim。TimeSlicing 與 MPS 共用 `performance-gpu` ResourceClaim，runner 按 N 建立 `benchmark-lead` 與 `benchmark-partner-*` Job。

原始完整 YAML 的對應見 [Manifest 來源](manifests/SOURCES.md)。可以離線檢視渲染結果：

```sh
$ kubectl kustomize manifests/overlays/mps --load-restrictor=LoadRestrictionsNone
```

ConfigMap 從 [worker.py](workload/worker.py)、[policy.py](workload/policy.py) 與 [control.py](workload/control.py) 產生，所以需要放寬相鄰來源目錄的載入限制。直接 `apply -k` 不會送出 START，應使用 `run.sh` 完成原本的協調流程：

1. 核對 publisher、DeviceClass 與 GPU 閒置狀態，保存輸入，開始原生 NVML 取樣。
2. 建立 Claim 與 Job，等待每個 worker warmup 後 READY，核對工作設定摘要、image 摘要、UUID、Pod UID 與主機 monotonic clock domain。
3. 讀取節點主機的 monotonic 時間，將共同 release 設為三秒後；全部 armed 必須在 release 前完成，每個開始延遲及最大 start skew 都不超過 20 ms。
4. 等全部 END，停止計時取樣，再讓全部 Task 做八點 float64 參照驗證，最後同步釋放 GPU 並退出。exit code 必須為 0，container 不得重啟。
5. 保存日誌、控制狀態、實際 Claim、終止 container 資訊與取樣；清理本批次，確認 GPU 回到閒置狀態，才開始下一批。Sequential 每個 Task 都完整走一次這個流程。

MPS 在 READY 後要求 compute mode 為 `Exclusive_Process`，且 GPU process 清單至少包含一個 `nvidia-cuda-mps-server`。其餘項目須是本批 worker：控制端讀取主機 `/proc`，將 NVML 的主機 PID 對照到 READY 的 container PID、UID、Pod 名稱、Pod UID 與 run nonce，並核對 worker 命令。查詢的完整 `pid,process_name,used_memory` 清單及身分對照保存在 `mps-runtime.json`；server 與 client 同時列出是可接受的情況，見[第 4 章 MPS 的觀測](../../book/04-isolation/mps.md)。

[worker.py](workload/worker.py) 與原 worker 位元組相同，固定工作量、同步、READY、START、END 及數值驗證均未改動。新增的控制端只負責遠端協調與 Kubernetes 物件生命週期；GPU 時間戳與取樣時間戳始終來自同一個節點時鐘。

## 收集與分析

所有輸出都寫入 `out/<run-id>/`，不覆寫其他 run-id。`commands.jsonl` 保存 kubectl 的命令、exit code 及輸出；執行失敗時保存 `run-error.json` 並嘗試收集仍存在的 Pod。完整結果大於書中精選資料，請在本機保存：

```text
out/<run-id>/
  run.json, benchmark-config.json, matrix.json, versions.json
  commands.jsonl, completed.json
  batches/<trial-id>/
    cell.json, manifests.json, idle-before.json, idle-after.json
    task-*.log, control-final.json, identity.json, trial.json
    terminated-pods.json, claims-during.json, mps-runtime.json（MPS）
    observer-stream.jsonl, observer-ready.json, observer-final.json
    gpu-metrics.jsonl, gpu-raw.csv, complete.json
  analysis/summary.csv, tables.md, aggregated.json, paired-ratios.json
  collections/<timestamp>/objects.json, events.json, *.log
```

Sequential 的每個 Task 保存在批次內的 `serial-task-*/`，批次 makespan 加總實際 START 至 END 的服務時間。它排除建立 Pod、warmup、驗證、清理及批次間等待，和原報告的基準定義一致。

```sh
$ bash scripts/collect.sh smoke-a40
$ python3 analysis/analyze.py out/smoke-a40/batches --config out/smoke-a40/benchmark-config.json --output out/smoke-a40/reanalysis
```

分析器拒絕缺少事件、設定摘要不符、工作次數不符、clock domain 不符、超過 20 ms 的開始延遲、錯誤數值、非零 exit code、container 重啟、未確認清理或不完整 NVML 取樣的批次。GPU 使用率採用各樣本左側持續值的時間加權積分；P95 使用線性插值。章節表格的 P95 合併同一條件的全部 Task 延遲，不是五個批次 P95 的平均。

原正式表格可完全離線重算，與 [report-tables.md](../../data/e3/report-tables.md) 對照：

```sh
$ python3 analysis/analyze.py ../../data/e3/summary.csv --output out/original-tables
$ python3 analysis/analyze.py ../../data/e3/raw --output out/golden-metrics
```

`data/e3/raw/` 保留每種模式一個 continuous 完整批次。它足以驗證每批的 makespan、Task/s、延遲、GPU 使用率與數值檢查；其餘正式批次由原始 180 列 summary、Task timing、group statistics 與 reconciliation 保存，來源見 [SOURCES.md](../../data/e3/SOURCES.md)。原 continuous 共用 Speedup 約 0.93–0.96，間歇工作最高約 2.33；自己的新結果應先確認有效，再按工作與 block 配對比較，不以這些倍數作為必須通過的門檻。

## 清理與中斷處理

先收集，再清理：

```sh
$ bash scripts/collect.sh smoke-a40
$ bash scripts/cleanup.sh smoke-a40
```

每批清理只刪除 `gpu-performance-lab` 中同時帶 `app.kubernetes.io/part-of=dra-book` 與該 run nonce 的 Job、Pod、ResourceClaim、ResourceClaimTemplate、ConfigMap。刪除前核對 UID 與 spec，API DELETE 帶 UID 前置條件；活著的 worker 先接收正常停止，完成同步釋放後才刪除。明確執行 `cleanup.sh` 時，確認 GPU 閒置後，會再刪除帶 `dra-book` label 的空 Namespace，並等待它消失；刪除前列出所有資源，除 Kubernetes 預設 ServiceAccount、根憑證 ConfigMap 與 events 外，有任何殘留物件便停止，Namespace 刪除也帶 UID 前置條件。

已完成的 run-id 可重複執行而不建立新 Task；collect 與 cleanup 也可重複執行。只有 provider 邊界允許用相同 run-id 續跑；若某個 CUDA 批次已開始但未完整結束，runner 拒絕重跑該批次，請保留證據、清理後換新的 run-id。所有收集輸出留在 gitignored 的 `out/`，公開前須另外檢查敏感資訊。

## 實機驗證紀錄

2026-10-12 已在原叢集完成縮短版：exclusive、sequential、TimeSlicing、MPS 與 HAMi 五種模式都完成，趨勢與原報告一致（同步間歇 N=2 時 MPS 1.951、TimeSlicing 1.425、HAMi 1.508）；原生分享設定與 HAMi provider 的切換都經獨立快照核對還原。實際命令與結果見[實機驗證紀錄](VERIFIED.md)。
