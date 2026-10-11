# 暫時切換 A40 的 DRA provider

> **警告：這些腳本會修改既有 DaemonSet、刪除 A40 kubelet plugin Pod 及其暫留的 ResourceSlice。只在專用實驗叢集執行，先停止 GPU 工作並清理 ResourceClaim；勿在正式服務的節點操作。**

原始 NVIDIA DaemonSet 為 `nvidia-dra-driver-gpu/dra-driver-nvidia-gpu-kubelet-plugin`，selector 為 `dra-driver-nvidia-gpu-component=kubelet-plugin`。預設切換 `dra-a40`，並保護 `dra-4090` 上同一個 DaemonSet 的 plugin Pod；Pod UID 改變時腳本會停止。節點 affinity、容忍條件、映像檔與原始更新策略都取自這次執行的備份。

## 備份與排除節點

需要 Bash、Python 3 與已能存取實驗叢集的 `kubectl`。Python 輔助程式只處理本機 JSON；叢集操作均由 `kubectl` 執行並寫入 `commands.log`。所有腳本都要求明確設定確認變數，包含唯讀的備份步驟。

從儲存庫根目錄執行以下命令，讓 provider 切換與工作負載共用同一份輸出目錄。`OUT_DIR` 優先於 `RUN_ID`；若未設定 `OUT_DIR`，`RUN_ID` 會選取 `labs/04-isolation/out/<run-id>`。排除與還原不會自動挑選最近的備份。

```bash
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ export RUN_ID=isolation-smoke
$ export OUT_DIR="$PWD/labs/04-isolation/out/$RUN_ID"
$ bash labs/04-isolation/provider/backup.sh
$ bash labs/04-isolation/provider/exclude-node.sh dra-a40
```

若要調整節點，在第一次備份前設定 `GPU_NODE` 與 `PROTECTED_NODE`；兩者必須各有一個原始 plugin Pod，且不能相同。`backup.sh [run-id]` 也接受命令列 run-id。完成的備份不會被再次執行覆寫；不完整的備份會保留供檢查，需使用新的 run-id。

備份位於 `out/<run-id>/provider-backup/`，包含 `original-daemonset.yaml`、`original-daemonset.json`、`uid.txt`、`update-strategy.json`、原始 Pod、ResourceSlice、Node JSON 與 `backup.json`。後續 JSON patch 會在 API server 上先測試原始 UID 與剛讀取的 spec，再替換 spec；刪除使用 `DeleteOptions` 的 UID 與 resourceVersion 前置條件，避免同名物件被重建後遭刪除。[kubectl 原始 DELETE 介面](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_delete/)

排除節點時，每個原始 `nodeSelectorTerms` 都會保留既有條件，附加 `matchFields` 的 `metadata.name NotIn [dra-a40]`；`updateStrategy` 改為 `OnDelete`。腳本只刪除備份記錄的 A40 plugin Pod。DELETE 若遇到 Conflict 或 NotFound，會重新讀取 Pod；只有它已消失，或仍符合備份 UID 且已有 `deletionTimestamp`，才繼續等待，不會改用新的 resourceVersion 重試刪除。不同 UID 或沒有終止標記的版本變動都會停止；這個過程不會主動替換 4090 plugin。

原始實驗中的 ResourceSlice 由 Node 持有，因此 plugin Pod 消失後仍可能留在 API server。腳本確認原始 plugin 已消失、沒有替代 provider、沒有待清理的 Claim，並核對 ResourceSlice UID 與 spec 完全符合備份後，才刪除這些暫留的原始 slices；接著確認 A40 不再被公布。若 slices 已經消失，這一步會略過。

每次修改 DaemonSet 或刪除原始 plugin 與 slices 前，腳本都會透過 `kubectl -n gpu-operator exec` 執行 `nvidia-smi`。目標 GPU UUID 必須符合備份，GPU 記憶體使用量不得超過 `64 MiB`，compute 行程清單必須為空，compute mode 必須是 `Default`；這與原始切換步驟的閒置門檻相同。原始查詢輸出保留在備份目錄中的 `*-gpu-idle.csv` 與 `*-compute-apps.csv`。

## 安裝替代 provider 與還原

排除完成後，依 [ConsumableShares 實驗步驟](../consumable-shares/README.md) 安裝替代 provider，再執行工作負載。執行結束先清理該實驗的 Jobs、Pods 與 ResourceClaims，再移除替代 provider 的 DaemonSet 及 Pods，最後清除它留下的 ResourceSlices 並還原原始 NVIDIA provider。

```bash
$ DRIVER=gpu.nvidia.com bash labs/04-isolation/provider/remove-provider-slices.sh
$ bash labs/04-isolation/provider/restore.sh
```

`remove-provider-slices.sh [node]` 供替代 provider 移除步驟共用；HAMi-DRA 使用 `DRIVER=hami-core-gpu.project-hami.io`。原始 DaemonSet 必須仍處於排除狀態，目標節點不能存在替代 plugin 或待清理的 Claim，GPU 也必須符合閒置檢查。安裝若在排除階段中止，腳本會先在 `PROVIDER_TIMEOUT` 內等待備份 UID 的終止中原始 Pod 與原始 slices 消失；逾時則停止，提示使用同一個 `OUT_DIR` 完成 `exclude-node.sh` 再重試移除。待刪除的替代 slice 必須符合目標 nodeName、pool、driver、GPU UUID 與 Node owner UID；腳本先保存 `alternative-slices.json`，再次讀取並核對 UID/spec 後，才以 UID/resourceVersion 前置條件刪除。其他節點的 slices 不會納入刪除清單。

還原會拒絕仍存在的替代 provider Pod 或 DaemonSet，以及 `gpu-isolation-lab` 內的任何 ResourceClaim；其他 Namespace 若有配置到目標節點 pool 的 Claim，也會阻止還原。原始 provider 仍被排除時，A40 上若殘留任何 ResourceSlice，腳本同樣會停止，避免兩個 provider 同時公布同一張 GPU。

還原先在 `OnDelete` 下恢復原始 Pod 範本，等 A40 plugin Ready 且原始 ResourceSlice 的裝置、屬性與容量回復，再恢復原始 `updateStrategy`。最後核對 DaemonSet UID、spec 與 4090 plugin UID；spec 比較只略過 Pod 範本 metadata 的伺服器管理欄位，slice 比較略過重新公布後會改變的 pool generation。結果記錄於 `restored.json` 與 `restored-daemonset.yaml`。

`PROVIDER_TIMEOUT` 控制等待秒數，預設為 `300`；`WAIT_INTERVAL` 控制輪詢間隔，預設為 `2`。逾時或身份核對失敗時，腳本會留下完整紀錄，且不會自動安裝、移除其他 provider 或覆寫外部修改；在同一個 `OUT_DIR` 再次執行可繼續已完成的排除或還原階段。MPS 使用原始 provider，直接依 [MPS 實驗步驟](../mps/README.md) 執行即可。
