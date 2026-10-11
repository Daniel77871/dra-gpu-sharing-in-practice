# 實機驗證紀錄：02-setup

本紀錄是在原叢集上實際執行本 lab 縮短版（`SMOKE=1`）的結果。本 lab 的完整模式與縮短模式內容相同。

## 環境

| 項目 | 值 |
| --- | --- |
| 執行日期 | 2026-10-11（UTC 11:49） |
| 執行位置 | 控制平面節點 `dra-m1`（Python 3.12.3、Helm v4.3.0） |
| Kubernetes | v1.37.0 |
| GPU Operator | v26.7.0（driver 595.91.07） |
| NVIDIA DRA Driver | 0.5.0（kubelet plugin DaemonSet 維持 `RollingUpdate`，2/2 Ready） |
| GPU | `dra-a40`：NVIDIA A40；`dra-4090`：NVIDIA GeForce RTX 4090 |

## 執行的命令

```console
$ bash labs/02-setup/scripts/verify.sh smoke-verify
$ SMOKE=1 bash labs/02-setup/scripts/run.sh smoke-20261011
$ bash labs/02-setup/scripts/cleanup.sh
```

`install.sh` 與 `restore.sh` 只適用於全新叢集，這次沒有執行；既有部署以 `verify.sh` 的唯讀檢查確認。

## 結果

`verify.sh`：inventory 與兩個 Helm release 的 values 檢查都通過。GPU Operator release 另有一個使用者覆寫值 `dcgmExporter`（之後為監控加入），只列為 INFO，不影響本章設定。

```text
PASS: inventory
Helm user values: gpu-operator
INFO: additional Helm user value: dcgmExporter
PASS: values
Helm user values: dra-driver-nvidia-gpu
PASS: values
```

`run.sh`（exit 0）：

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

```text
node | GPU | claim allocated | claim deleted after Pod deletion | pytorch CUDA ok
--- | --- | --- | --- | ---
dra-a40 | NVIDIA A40 | yes | earlier | yes
dra-4090 | NVIDIA GeForce RTX 4090 | yes | earlier | yes
```

兩個 workload 的實際輸出：

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

## Claim 生命週期的實際觀察

- 生成的 claim 名稱是 `gpu-smoke-a40-v5p7w-gpu-5rnbh`，也就是「Pod 名稱 + Pod 內的 claim 引用名 `gpu` + 隨機尾碼」。claim 帶有 `resource.kubernetes.io/delete-protection` finalizer，`ownerReferences` 指向該 Pod。
- 配置結果是 `driver: gpu.nvidia.com`、`pool: dra-a40`、`device: gpu-0`。`nodeSelector` 以 `metadata.name In [dra-a40]` 限定節點，`reservedFor` 只列出該 Pod。
- Job 完成後、刪除 Job 之前，兩份生成的 claim 已經不存在（`claim_absent_before_job_deletion: true`）。這和 Kubernetes v1.37.0 的行為一致：Pod 終止後，ResourceClaim 控制器移除 reservation 並刪除該 Pod 產生的 claim，不必等 Pod 物件被刪除。

## 清理

`cleanup.sh` 刪除了兩個 ResourceClaimTemplate、ConfigMap 與 `gpu-setup-lab` Namespace。之後叢集上沒有任何 ResourceClaim，兩張 GPU 回到空閒。

## 結論

通過。部署狀態與文件一致；A40 與 RTX 4090 都能透過 ResourceClaimTemplate 取得整卡並執行 CUDA；claim 的建立、配置與回收和第 2.4 節的說明相符。

## 以最終版 manifests 重跑

CEL 條件改寫成單行引號字串後（語意不變），同日以 `SMOKE=1 bash labs/02-setup/scripts/run.sh smoke-20261011-r2` 再跑一次。結果相同：兩張 GPU 都配置成功、PyTorch CUDA 檢查通過，生成的 claim 在刪除 Job 前已回收；`cleanup.sh` 後沒有殘留 ResourceClaim。
