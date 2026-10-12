# 部署 GPU Operator 與 NVIDIA DRA Driver

這套 lab 檢查 Node Ready、GPU Operator、每台 GPU 節點的 DRA plugin、三個 DeviceClass、型號正確的 ResourceSlice 與 Helm values。再用 A40、RTX 4090 各一個整卡 Job 執行 `nvidia-smi -L` 和 PyTorch CUDA 檢查，保存 claim 的分配與回收過程；對應[第 2 章](../../book/02-deploy/README.md)。

## 前置條件

| 項目 | 本次環境或必要條件 |
| --- | --- |
| host | Ubuntu 24.04.3 LTS、GA kernel 6.8 系列；PCI passthrough、nouveau 與 headers 已按[節點準備](../../book/02-deploy/node-prep.md)完成 |
| 節點與 GPU | dra-m1、dra-non、dra-a40、dra-4090 皆 Ready；GPU 節點分別為 NVIDIA A40、NVIDIA GeForce RTX 4090 |
| 叢集 | Kubernetes v1.37.0、CRI-O 1.37.1、Cilium 1.20.2，能執行一般 Pod |
| GPU 元件 | GPU Operator v26.7.0、driver 595.91.07、DRA Driver 0.5.0 |
| 命令列 | Bash、kubectl、Helm、python3；Python 分析只用 stdlib |
| 權限 | 讀 Node、元件 Pod、DeviceClass、ResourceSlice、Helm release；管理 gpu-setup-lab 及 exec 自己的 workload |
| image | `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755` |

兩台 GPU 已使用相同 digest，`IfNotPresent` 可重用 cache；cache 被移除時仍可能拉取相同 image。Lab 不修改 host 套件、kernel、initramfs 或開機方式。

## 既有叢集執行順序

在儲存庫根目錄依序驗證、執行、收集、清理。完整模式省略 `SMOKE=1`，兩種模式內容相同；再次執行工作請換新的 run-id，已有 summary 的 run-id 只顯示保存結果。

```console
$ bash labs/02-setup/scripts/verify.sh smoke-verify
$ SMOKE=1 bash labs/02-setup/scripts/run.sh smoke-20261011
$ bash labs/02-setup/scripts/collect.sh smoke-20261011
$ bash labs/02-setup/scripts/cleanup.sh
```

`verify.sh` 唯讀，Helm values 依內容比較，鍵的順序不影響結果。`run.sh` 先對兩台節點做 GPU 空閒檢查，保存 allocation 後才放行 Job，完成後收集並以 Background 刪除 Job；`collect.sh` 可重跑，保留先前保存的 logs。`cleanup.sh` 只刪 gpu-setup-lab 內標示為 `app.kubernetes.io/part-of=dra-book` 的物件，再刪自己的 Namespace；有外來物件或 API 錯誤便停止。

## 實機結果

2026-10-11 在原叢集執行縮短版通過，完整命令、部署檢查與清理結果見[實機驗證紀錄](VERIFIED.md)。Helm values 兩個 release 都相符；額外的 `dcgmExporter` 覆寫值列為 INFO。以下是 run.sh 的實際輸出，exit 0：

```text
PASS: GPU idle check on dra-a40 (memory.used < 100 MiB)
PASS: GPU idle check on dra-4090 (memory.used < 100 MiB)
namespace/gpu-setup-lab created
resourceclaimtemplate.resource.k8s.io/a40-whole-gpu created
resourceclaimtemplate.resource.k8s.io/rtx4090-whole-gpu created
configmap/gpu-setup-workload created
job.batch/gpu-smoke-a40 created
job.batch/gpu-smoke-rtx4090 created
pod/gpu-smoke-a40-v5p7w condition met
pod/gpu-smoke-rtx4090-9b9rw condition met
```

兩個 Pod 的完整 logs，依序為 A40、RTX 4090：

```text
WAITING_FOR_CLAIM_SNAPSHOT
GPU 0: NVIDIA A40 (UUID: GPU-29ffee3e-bee4-e341-917a-fe7e43e977af)
PYTORCH_CUDA_OK NVIDIA A40
```

```text
WAITING_FOR_CLAIM_SNAPSHOT
GPU 0: NVIDIA GeForce RTX 4090 (UUID: GPU-0c6a9ee3-89b0-d48f-de2c-758c2cedcd1e)
PYTORCH_CUDA_OK NVIDIA GeForce RTX 4090
```

以下是 summary.json 經 summarize.py 輸出的實際表格：

```text
node | GPU | claim allocated | claim deleted after Pod deletion | pytorch CUDA ok
--- | --- | --- | --- | ---
dra-a40 | NVIDIA A40 | yes | earlier | yes
dra-4090 | NVIDIA GeForce RTX 4090 | yes | earlier | yes
```

兩份 claim 都在刪除 Job 前已消失（`claim_absent_before_job_deletion: true`），因此摘要為 `earlier`，符合 Kubernetes v1.37.0 在 Pod 終止後回收產生 claim 的行為。[完整 claim YAML 與生命週期解釋](../../book/02-deploy/verify.md#觀察-claim-生命週期)說明了 allocation、nodeSelector 與 reservedFor 的讀法。

## 新叢集安裝與還原

> **警告：install.sh 只供全新的 GPU 部署，會改變節點 label、叢集層級 DeviceClass、CRD 與 GPU 元件。已有 GPU Operator 或 DRA Driver 的叢集不要執行；不要用它切換 provider 或升級既有 driver。**

- 安裝前要求元件 Namespace、release、NVIDIA DeviceClass 與 ResourceSlice 不存在，先備份 Node、release 與所有原有 DaemonSet；先安裝 GPU Operator 並等待 driver、nvidia-smi 與服務就緒，再安裝 DRA。
- [GPU Operator values](values/gpu-operator.yaml) 與 [DRA values](values/dra-driver.yaml) 精確對應原指南的 `--set`。原步驟未設定 eviction 所需的 DRA nodeSelector，driver 升級的驅逐與恢復仍未驗證，見[安裝限制](../../book/02-deploy/dra-driver.md#限制)。
- 還原前先清理 GPU 工作並保留安裝備份；restore.sh 比對原有與本次 DaemonSet 的 UID/spec，拒絕仍有已分配 claim、外來物件或查詢錯誤的狀態。它依 DRA、Operator 順序移除元件，再恢復手動 label。
- 還原只處理本次 Kubernetes 部署，不能代替 VM 快照，也不回退 kernel、host 套件、runtime 設定、NFD 自動 label 或已載入的 driver 模組。這次實機紀錄未執行 install.sh 或 restore.sh。

```console
$ FRESH_CLUSTER=1 bash labs/02-setup/scripts/install.sh setup-install
$ bash labs/02-setup/scripts/verify.sh setup-install-check
$ bash labs/02-setup/scripts/cleanup.sh
$ bash labs/02-setup/scripts/restore.sh setup-install
```

## 輸出檔案

檔案保存於 `out/<run-id>/`，清理叢集時保留。可用 `python3 labs/02-setup/analysis/summarize.py labs/02-setup/out/<run-id>` 再印摘要。

| 檔案 | 內容 |
| --- | --- |
| `run.json`、`commands.log` | run-id、模式、開始時間、kubectl 命令及 exit code |
| `idle-dra-a40/`、`idle-dra-4090/` | 各節點的 GPU 空閒檢查 |
| `<role>-claim-allocated.yaml`、`.json` | 放行前的 allocation 與 reservedFor |
| `<role>-claim-completed.yaml`、`.json` | 完成後的 claim；不存在時 YAML 為空、JSON 為 `{}` |
| `<role>-pod-allocated.json`、`<role>-pod-completed.yaml`、`.json` | 執行中及完成後的 Pod |
| `<role>-pod.log`、`events.yaml`、`events.json` | workload logs 與 Namespace 事件 |
| `<role>-deletion.json` | 刪除請求、首次觀察到 Pod/claim 不存在的時間與是否較早回收 |
| `summary.json`、`run-error.txt` | 摘要；失敗時保存 error 與錯誤訊息 |

刪除時間是首次成功查詢到 NotFound 的 UTC 時間，並非 API server 精確時間或效能量測。`SETUP_TIMEOUT` 預設 300 秒、`WAIT_INTERVAL` 預設 1 秒；API 讀取失敗不算資源已消失。

更新 workload 後，可在 lab 目錄用 client dry-run 重新產生 ConfigMap：

```console
$ kubectl create configmap gpu-setup-workload --namespace=gpu-setup-lab --from-file=gpu_smoke.py=workload/gpu_smoke.py --dry-run=client -o yaml | kubectl label --local -f - app.kubernetes.io/part-of=dra-book app.kubernetes.io/component=cuda-check -o yaml > manifests/03-workload.yaml
```
