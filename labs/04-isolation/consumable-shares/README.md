# ConsumableShares 排程記帳與 runtime 測試

本套件在 A40 的 NVIDIA DRA v0.5.0 provider 啟用 `ConsumableShares`，分別檢查 16／24／8 GiB 的 Claim 記帳與 8 GiB Claim 的 CUDA 配置行為。完整機制、部署 YAML 與量測結果見[第 4.1 節](../../../book/04-isolation/consumable-shares.md)。

> **實驗叢集限定：安裝與移除 provider 會修改原始 NVIDIA DaemonSet，刪除 `dra-a40` 的 plugin Pod 和暫留的 ResourceSlice。先停止 GPU 工作、清理 Claims，保留 `out/<run-id>/provider-backup/`，並完成 UID／spec／Slice 還原核對。**

## 前置條件

依[共用前置條件](../README.md#前置條件)準備叢集。原始 DaemonSet 是 `nvidia-dra-driver-gpu/dra-driver-nvidia-gpu-kubelet-plugin`；`dra-a40` 與 `dra-4090` 各須有一個 Ready plugin Pod，後者的 UID 在切換期間受到核對。本套件重用第 2 章的 `dra-driver-nvidia-gpu-service-account-kubeletplugin` 與原 ClusterRole／Binding；安裝腳本讀取並核對必要權限，不改寫原始 RBAC。

替代 provider 原本是一個固定在 A40 的 Pod，這裡以 `OnDelete` DaemonSet 包裝成 `a40-consumable-provider`。原始五組 affinity 的每個 OR term 都增加 `metadata.name In [dra-a40]`；image 固定為 `nvcr.io/nvidia/dra-driver-nvidia-gpu:v0.5.0@sha256:2f76a02532bea530418693bcf382f2094cb40dc8466cefdbb9af923a232e738b`，其餘環境變數、privileged、mounts、probe 與 ServiceAccount 沿用原實驗。改動詳見[provider 說明](../provider/README.md)。

## 安裝與執行

從儲存庫根目錄執行，provider 安裝、workload 與移除必須使用同一個 run-id。`I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1` 是每個 provider 腳本的必要條件；腳本先印出修改內容才操作。

```sh
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash labs/04-isolation/consumable-shares/scripts/install-provider.sh capacity-smoke
$ SMOKE=1 bash labs/04-isolation/consumable-shares/scripts/run.sh capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/collect.sh capacity-smoke
$ python3 labs/04-isolation/common/analysis/analyze.py labs/04-isolation/out/capacity-smoke --output labs/04-isolation/out/capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/cleanup.sh capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/uninstall-provider.sh capacity-smoke
```

完整模式省略 `SMOKE=1`，預設記帳與 runtime 各三輪。`MODE=accounting` 或 `MODE=runtime` 只選該測試；`SMOKE=1` 將選定條件各減為一輪，保留原始配置步長與 baseline／hold／recovery 時間。不要在已安裝替代 provider 時執行 MPS。

安裝先備份原始 DaemonSet、UID、更新策略、Pods 與 Slices，再改為 `OnDelete` 並排除 A40，刪除 A40 的原始 plugin，核對其 Slices 消失且 4090 plugin UID 不變，才套用替代 DaemonSet。若同一 run-id 已安裝過，腳本只在原生排除狀態、受保護 UID、替代 provider 完整 spec 與 A40 Slice 都一致時完成；遇到外部修改會停止。

## 記帳測試

`manifests/accounting/` 依序建立 `capacity-resident-gpu`（16 GiB）、`capacity-release-gpu`（24 GiB）及 `capacity-waiter-gpu`（8 GiB）和各自的 sleeper Job。每個 Job 觸發一份獨立 Claim；前兩個 Ready 後，第三個必須未配置並有 `cannot allocate all claims` 的 scheduler event。CPU／RAM 不足的事件會使本輪停止，不會算成 GPU 記帳成功。

收集第三個 Pending 的 Claims、events 與 GPU 實體用量後，先保存 release Job 日誌，再刪除它和 24 GiB Claim；第三個應變成 Ready 且取得 allocation。原始三輪都是公布 46,068 MiB、記帳餘額 5,108 MiB、實體 free 45,490 MiB；釋放後記帳餘額為 21,492 MiB。這些是[原始記帳摘要](../../../data/e2/accounting.csv)，實體空閒並不代表排程餘額足夠。

## Runtime 測試

`manifests/runtime/` 給 A 一份 4 GiB Claim、B 一份 8 GiB Claim，兩份 allocation 必須有不同 `shareID`，但裝置 driver／pool／device 和 CUDA UUID 相同。A 保留 2 GiB、每秒運算和 512 MiB probe；B 每次增加 256 MiB，真正配置 OOM 才 hold 30 秒。若成功 tensor 超過 8 GiB，B 同步退出，結果是 `limit_not_enforced`，沒有 hold 窗口。

原始三輪 B 都成功保留 8.25 GiB，A 在整段 pressure phase 分別成功 probe 10、9、9 次，沒有 probe OOM 或 compute errors；A、B 都正常退出。B 活躍期間的整卡 NVML 峰值為 10,354／10,866／9,586 MiB，不能用 B 退出後的 `gpu-during.txt` 代替峰值。這是原實驗的負結果：ConsumableShares 有排程記帳，沒有 CUDA 配置強制上限。

## 收集清理與還原

`collect.sh` 保存 API 快照與日誌並重算摘要；所有輪次自動先收集後清理，清理驗證後才印出本輪判定。中斷時先收集再執行 `cleanup.sh`，等 CUDA 工作正常退出、Pods 和 Claims 消失後，才執行 `uninstall-provider.sh`。手動清理最後會檢查 Namespace 的物件歸屬，刪除帶有 dra-book label 的 `gpu-isolation-lab` 並等待完成；provider 與備份保留到移除與還原步驟。

移除先核對原始 DaemonSet UID／spec、4090 plugin UID、沒有 Claims、GPU `Default` 且沒有 compute process、用量最多 64 MiB，再刪除標記的替代 DaemonSet。它只刪除已保存並再次核對 UID／spec／Node owner 的 A40 暫留 Slice；最後在 `OnDelete` 下恢復原範本，待 A40 Ready 且 Slices 回復後再恢復原始更新策略。原始 UID、spec 和受保護 UID 的最終核對保存為 `provider-backup/restored.json`。

若切換過程中斷，保留備份與 `commands.log`，依 [provider 還原流程](../provider/README.md#安裝替代-provider-與還原)處理；不要重新安裝另一個 provider 或覆寫原始備份。原叢集縮短版的結果見[實機驗證紀錄](../VERIFIED.md#consumablesharesprovider-切換窗)。
