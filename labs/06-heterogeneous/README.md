# 異質 GPU 需求與排程

這套實驗把 A40 與 RTX 4090 放進同一個可選集合，先用一般 Job 驗證需求篩選，再用 Kubeflow Pipelines 驗證 Task 與 ResourceClaimTemplate 的整合。閱讀說明見[第六章](../../book/06-heterogeneous/README.md)；縮短版在原叢集的執行結果見[實機驗證紀錄](VERIFIED.md)。

請從完整 clone 的儲存庫根目錄執行命令，保留 `labs/`、`data/` 與 `tools/`。收集器與分析器只使用 Python 標準函式庫；原始結果的重算會讀取 `data/e4/` 與 `data/e4b/`。

## 前置條件

依[第二章的安裝實驗](../02-setup/README.md)建立 GPU Operator 與 NVIDIA DRA driver，確認 `dra-a40`、`dra-4090` 各有一張 GPU，且 GPU Operator driver Pod 可執行 `nvidia-smi`。操作者的電腦須有 `kubectl` 與 Python 3；不需要 SSH 到 GPU 節點。

Native 部分需要 `gpu.nvidia.com` DeviceClass，以及兩張 GPU 的原生獨占 ResourceSlice。Capacity 部分需要 HAMi-DRA 在兩個 GPU 節點發布可累加的 `memory` 與 `cores`；可用下面的獨立 provider 切換步驟建立。執行前應清除其他 GPU 工作，避免既有 Claim 或 CUDA 行程改變可用容量。

## 一般 Job 的縮短版

Native 七個情境各執行一次；`prefer-4090` 內含兩個步驟。`nonexistent` 會保留一段 Pending 觀察時間，記錄同一 Pod UID 的 `FailedScheduling` event，再刪除該 Job 與 Claim。

```bash
$ PART=native SMOKE=1 bash labs/06-heterogeneous/scripts/run.sh native-smoke
$ bash labs/06-heterogeneous/scripts/collect.sh native-smoke
$ python3 labs/06-heterogeneous/analysis/analyze.py --out labs/06-heterogeneous/out/native-smoke --output labs/06-heterogeneous/out/native-smoke/summary.json
$ bash labs/06-heterogeneous/scripts/cleanup.sh native-smoke
```

| 情境 | 需求 | 通過條件 |
|---|---|---|
| `a40` | `productName == "NVIDIA A40"` | CUDA 工作位於 `dra-a40` |
| `rtx4090` | `productName == "NVIDIA GeForce RTX 4090"` | CUDA 工作位於 `dra-4090` |
| `any-gpu` | DeviceClass，沒有額外 selector | 任一 GPU 完成 CUDA 正確性檢查 |
| `any-gpu-pair` | 兩個 Job，各自建立 Claim | 兩個 GPU 都有同時持有配置的工作 |
| `attribute` | `architecture == "Ampere"` | CUDA 工作位於 `dra-a40` |
| `nonexistent` | 不存在的 GPU UUID | 保持 Pending、出現排程失敗 event，清理後 Claim 消失 |
| `prefer-4090` | `firstAvailable` 先 RTX 4090、後 A40 | 空閒時驗收 `dra-4090`／`gpu/rtx4090`；4090 被 holder 占用時驗收 `dra-a40`／`gpu/a40` |

`prefer-4090` 的第一步在兩張 GPU 空閒時提交工作，第二步先讓一個使用普通獨占 `exactly` 請求的 holder 占用 RTX 4090，再使用相同 Template 提交工作。控制器先保存配置與 CUDA 正確性證據，再釋放 worker；第二步完成後釋放 holder。`trial.json` 的 `phases` 分別保存 `idle`、`rtx4090-held` 的落點、Claim UID 與 `request` 子請求，分析器也保留這兩個步驟；實際結果待新增至實機驗證紀錄。

## 切換兩個 GPU 的 provider

> **警告：這些命令會修改叢集範圍的 NVIDIA DaemonSet、DeviceClass 與 RBAC，並在 GPU 節點建立及移除 HAMi host 檔案。請安排獨立的 GPU 維護時段，保存整個 `out/provider-<provider-run-id>/`，並完成還原。**

Provider 操作重用[第四章的切換檢查](../04-isolation/provider/README.md)、HAMi manifest 與 host 檔案控制器。切換前先保存原 NVIDIA DaemonSet UID、完整 spec、節點 UID、plugin Pod UID、ResourceSlice 與 host 檔案快照；切換使用 `OnDelete`，只排除列出的 GPU 節點。刪除採用 UID 與 resourceVersion 前置條件；若 DaemonSet controller 已開始刪除 plugin Pod，會重新檢查原 UID 並等待其消失。

```bash
$ I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1 NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/install-provider.sh capacity-window
```

本實驗的 wrapper 預設 `NODES="dra-a40 dra-4090"`，原有第四章的單節點腳本維持原介面。新增的第四章 `*-nodes.sh` 腳本預設只處理 `dra-a40`；指定一個節點時，另一張 GPU 的原生 plugin Pod UID 受保護。安裝完成會保存 `provider-nodes-installed.json` 與 `provider-nodes-ready-slices.json`，檢查所有目標 GPU 都具有 consumable capacity 與 request policy。

Provider 操作可用相同 provider run-id 重試，已完成還原的 run-id 不可再安裝。任何 UID、spec、host 檔案身分或 SHA256 不符都會停止並保留證據；不要刪除快照或改用不同 run-id 覆寫失敗的切換。

## 容量需求的縮短版與完整模式

縮短版只執行 H1 與 H2 各一次；30 GiB 的需求沒有型號 selector，靠兩張卡的發布容量判定可選集合。完整模式每個情境執行三輪，並加入 H3-H4 與 H5。

```bash
$ PART=capacity SMOKE=1 bash labs/06-heterogeneous/scripts/run.sh capacity-smoke
$ bash labs/06-heterogeneous/scripts/collect.sh capacity-smoke
$ python3 labs/06-heterogeneous/analysis/analyze.py --out labs/06-heterogeneous/out/capacity-smoke --output labs/06-heterogeneous/out/capacity-smoke/summary.json
$ bash labs/06-heterogeneous/scripts/cleanup.sh capacity-smoke
$ PART=capacity bash labs/06-heterogeneous/scripts/run.sh capacity-full
$ bash labs/06-heterogeneous/scripts/collect.sh capacity-full
$ python3 labs/06-heterogeneous/analysis/analyze.py --out labs/06-heterogeneous/out/capacity-full --output labs/06-heterogeneous/out/capacity-full/summary.json
$ bash labs/06-heterogeneous/scripts/cleanup.sh capacity-full
```

| 情境 | 需求與控制 | 通過條件 |
|---|---|---|
| `h1-8gi-any` | 8 GiB memory、25 cores，不限型號 | 其中一張 GPU 通過 CUDA 檢查，不指定落點順序 |
| `h2-30gi-any` | 30 GiB memory、25 cores，不限型號 | 只有 A40 容量足夠，工作位於 `dra-a40` |
| `h3h4-pending-release` | A40 holder 使剩餘量低於 30 GiB | target 保持 Pending；holder 釋放後，原 Pod UID 與原 Claim UID 取得 GPU |
| `h5-two-slots` | 每張 GPU 的 holder 各留 8 GiB；再建立兩個 8 GiB target | 兩個 target 同時持有配置，分別位於兩張 GPU |

Holder 也使用 ResourceClaimTemplate，一個 Job Pod 對應一個 Claim。腳本依即時發布的 memory 與 request-policy step 計算 holder 配額，先確認 RTX 4090 容量小於 30 GiB、A40 容量至少 30 GiB；完整原始容量下的 holder 需求是 `15349Mi`、`37876Mi` 與 `16372Mi`，實際採用值記錄於 `holder-requests.json`。H3-H4 的 Pending 觀察至少 30 秒，釋放後同一個 target 才能進入配置屏障；這個本機觀察窗口與書中原實驗測得的 34.0 秒不是同一個數值。

指定部分情境可使用逗號或空白分隔的 `CASES`，但 `SMOKE=1 PART=capacity` 只允許 H1 與 H2。Native 完整模式同樣各執行三輪；每個 case 的 Job、Pod 與 Claim 在進入下一輪前都會刪除。

```bash
$ PART=native CASES="a40,attribute" bash labs/06-heterogeneous/scripts/run.sh native-subset
$ bash labs/06-heterogeneous/scripts/cleanup.sh native-subset
$ PART=capacity CASES="h3h4-pending-release,h5-two-slots" bash labs/06-heterogeneous/scripts/run.sh capacity-recovery
$ bash labs/06-heterogeneous/scripts/cleanup.sh capacity-recovery
```

## Kubeflow Pipelines

一般 Job 與 KFP 共用相同 CUDA primitives、固定映像檔與容量模型。Standalone KFP 的安裝、REST 提交與 h1／h2／l1 縮短版命令集中在[KFP 部分](kubeflow/README.md)；安裝與移除是獨立、受 guard 保護的叢集操作。Backend 使用 `dra-non`，GPU Task 保留一般排程。

執行 KFP 前仍須讓兩張 GPU 使用相同的 HAMi consumable capacity provider。KFP 工作完成並清理後，才能執行下面的 provider 還原命令。

## 收集與分析

`out/<run-id>/cases/<case>-r<round>/` 包含 `trial.json`、配置前後的 Pod／Claim JSON、ResourceSlice、scheduler event、Job log 與每個 GPU 的 500 ms CSV。取樣透過 `kubectl -n gpu-operator exec` 在 driver Pod 內執行 `nvidia-smi --query-gpu=... --format=csv -lms 500`；Pending container 尚未開始執行時不呼叫 `kubectl logs`。

`trial.json` 記錄落點、Pod UID、Claim UID、配置識別與清理是否完成；H3-H4 額外記錄 `same_pod_uid`、`same_claim_uid`、`pending_seconds` 與 `timestamps.json`。`claims_deleted` 是刪除 Job Pod 後觀察到 Claim 消失的證據，不代表一般 batch Job 一進入 Complete 就立即刪除其仍存在的 Pod。

原始 E4／E4B 的精簡資料也能離線重算，無須連線到叢集：

```bash
$ python3 labs/06-heterogeneous/analysis/analyze.py --output /tmp/heterogeneous-original-summary.json
```

輸出保留原實驗的 placement、Pending 窗口、失敗／取消／重試的 Claim 生命週期，以及 DRA 帳本與實體 GPU 空間的差異。兩種容量觀察不是同步快照，不能把 DRA remaining 當成 NVML physical free，也不能從本實驗推出最快 GPU 的選擇規則。

## 清理與 provider 還原

一般清理只刪除同一 run-id、帶有 `app.kubernetes.io/part-of: dra-book` 的物件，最後刪除自己的 Namespace 並等待消失。失敗時先用 `collect.sh` 保存現況，再用相同 run-id 清理；不要刪除其他實驗的物件。

```bash
$ bash labs/06-heterogeneous/scripts/cleanup.sh capacity-full
$ I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1 NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/restore-provider.sh capacity-window
```

還原先確認沒有 GPU Claim 或 CUDA 行程，停止所有目標節點的自有 HAMi DaemonSet，等待 terminating plugin Pod 消失，再移除已確認 GPU UUID、節點 UID、UID 與 spec 的 ResourceSlice。NVIDIA provider 先以 `OnDelete` 回復原 template，等待兩個原生 plugin Ready 與原生 ResourceSlice 回來，再回復原 updateStrategy；原 DaemonSet UID、完整 spec 與策略都核對相符後，才逐節點清理 host 檔案。

Host 清理重用第四章的 `host.py`；HAMi plugin 目錄內的 `pu.lock`、`cp.lock`、`checkpoint.json` 依既有允許清單處理。單一節點清理失敗會在 `provider-nodes-cleanup.json` 保存並回報錯誤，繼續處理其他節點，兩張 GPU 仍保有已還原的 NVIDIA provider；用相同 provider run-id 重試即可。全部清理完成才留下 `provider-nodes-restored.json`，原始備份仍留在 `out/`，以供檢視。

## 工作負載與限制

工作負載固定使用 `pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`。核心是 4096 × 4096 FP32 GEMM，seed 為 20261010，暖機 5 次、量測 20 次、TF32 關閉、CPU 執行緒為 1，並以 8 個元素的 FP64 CPU dot product 檢查結果；縮短版保留這些工作參數，只減少輪數與原生無匹配需求的觀察時間。

這套實驗驗證需求篩選、配置與釋放；H1 不要求重現原實驗三輪的落點次序，也不宣稱隨機性或效能最佳化。HAMi 配額的執行期範圍沿用[第四章的限制](../../book/04-isolation/README.md)。`selection/examples/attribute-requirements.yaml` 的多屬性 CEL 是未納入執行情境的延伸範例；`selection/examples/prefer-4090.yaml` 的 `firstAvailable` 則由 native 的 `prefer-4090` 案例執行。兩者都沒有在原 E4／E4B 執行。
