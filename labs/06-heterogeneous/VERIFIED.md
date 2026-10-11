# 實機驗證紀錄：06-heterogeneous

本紀錄是在原叢集上實際執行本 lab 縮短版（`SMOKE=1`）的結果。原生選卡使用第 2 章的 NVIDIA provider；容量選卡需要兩個 GPU 節點都暫時切換到 HAMi-DRA，跑完再還原。

## 環境

| 項目 | 值 |
| --- | --- |
| 執行日期 | 2026-10-12（UTC 2026-10-11 19:23–20:30） |
| 執行位置 | 控制平面節點 `dra-m1`，從完整 clone 的儲存庫根目錄執行 |
| Kubernetes | v1.37.0（`DRAPrioritizedList`、`DRAConsumableCapacity` 已啟用） |
| GPU | `dra-a40`：NVIDIA A40；`dra-4090`：NVIDIA GeForce RTX 4090 |

## 原生選卡（不切換 provider）

```console
$ PART=native SMOKE=1 bash labs/06-heterogeneous/scripts/run.sh native-smoke-20261012
$ PART=native CASES=prefer-4090 SMOKE=1 bash labs/06-heterogeneous/scripts/run.sh n6-prefer-20261012
```

| 情境 | 需求 | 實際落點 | 結果 |
| --- | --- | --- | --- |
| `a40` | CEL `productName == "NVIDIA A40"` | `dra-a40` | 通過，CUDA 正確 |
| `rtx4090` | CEL `productName == "NVIDIA GeForce RTX 4090"` | `dra-4090` | 通過，CUDA 正確 |
| `any-gpu` | 只指定 DeviceClass | `dra-a40` | 通過 |
| `any-gpu-pair` | 兩個「任何 GPU」Job 同時提交 | `dra-4090` 與 `dra-a40` 各一個 | 通過，兩個 Job 同時跑在不同 GPU |
| `attribute` | 依 E4 使用的屬性條件 | `dra-a40` | 通過 |
| `nonexistent` | 不存在的型號 | 維持 Pending 約 10.5 秒後清理 | 通過，記錄到 `FailedScheduling` 事件 |
| `prefer-4090`（兩張卡都空閒） | `firstAvailable`：先 RTX 4090、再 A40 | `dra-4090`，配置的子請求為 `gpu/rtx4090` | 通過 |
| `prefer-4090`（4090 被 holder 占用） | 同一份 Template | `dra-a40`，配置的子請求為 `gpu/a40` | 通過 |

`prefer-4090` 是原始 E4／E4B 沒有執行過的情境，這次第一次在原叢集驗證：偏好順序由 `firstAvailable` 表達，第一個選項不可用時，scheduler 自動改用第二個選項。所有生成的 Claim 在 Job 結束後都已刪除。

## 容量選卡（provider 切換窗 3：兩個 GPU 節點都換成 HAMi-DRA）

```console
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/install-provider.sh capacity-window-20261012
$ PART=capacity SMOKE=1 bash labs/06-heterogeneous/scripts/run.sh cap-smoke-20261012
$ NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/restore-provider.sh capacity-window-20261012
```

| 情境 | 需求（沒有指定節點或 GPU） | 實際落點 | 結果 |
| --- | --- | --- | --- |
| H1 | `memory: 8Gi`、`cores: 25` | `dra-4090` | 通過；兩張卡都符合，這次選到 4090 |
| H2 | `memory: 30Gi`、`cores: 25` | `dra-a40` | 通過；只有 A40 公布的容量足夠 |

H2 再次確認「依容量自動選卡」：請求沒有任何型號條件，RTX 4090 的 24,564 MiB 不足 30 GiB，所以只會落在 A40。H1 的落點只說明兩張卡都可行，不代表隨機或效能最佳的選擇。

還原後以獨立快照比對原生 DaemonSet（UID、`RollingUpdate`、`FEATURE_GATES`、node affinity、spec 雜湊）與執行前相同，兩個節點的原生 plugin 與 ResourceSlice 都恢復；兩個節點上的 HAMi host 路徑（`/usr/local/vgpu`、HAMi plugin 目錄、`/tmp/vgpulock`）都已不存在，`/var/run/cdi` 與 kubelet plugin registry 和切換前相同。

## Kubeflow Pipelines（provider 切換窗 3b：兩卡 HAMi-DRA）

```console
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/install-provider.sh capacity-window-20261012-b
$ export KFP_RUN_ID=w3bkfp20261012 KFP_STATE_DIR="$PWD/labs/06-heterogeneous/kubeflow/out/w3bkfp20261012/kfp-install"
$ ALLOW_KFP_INSTALL=1 bash labs/06-heterogeneous/kubeflow/scripts/install-kfp.sh
$ SMOKE=1 bash labs/06-heterogeneous/kubeflow/scripts/run.sh
$ bash labs/06-heterogeneous/kubeflow/scripts/collect.sh
$ ALLOW_KFP_UNINSTALL=1 bash labs/06-heterogeneous/kubeflow/scripts/uninstall-kfp.sh
$ NODES="dra-a40 dra-4090" bash labs/06-heterogeneous/scripts/restore-provider.sh capacity-window-20261012-b
```

KFP 以固定 digest 安裝在 `dra-non`，獨立 Namespace 為 `gpu-kubeflow-w3bkfp20261012-lab`。每個 Task 只引用 ResourceClaimTemplate，沒有指定節點或 GPU：

| 管線 | 需求 | KFP Run | 實際落點 | Claim |
| --- | --- | --- | --- | --- |
| H1 | `memory: 8Gi`、`cores: 25` | SUCCEEDED | `dra-a40`（`hami-gpu-0`） | 結束後刪除 |
| H2 | `memory: 30Gi`、`cores: 25` | SUCCEEDED | `dra-a40`（只有 A40 容量足夠） | 結束後刪除 |
| L1 | 同 H1，CUDA 成功後受控以 42 退出 | FAILED（預期） | `dra-a40` | 失敗後仍刪除 |

這重現了 E4B 的兩個重點：KFP Task 透過 Template 自動取得 GPU，Claim 跟著 Task 的生命週期回收；即使 Task 失敗，配置也會釋放。H1 這次落在 A40，與窗 3 的 H1（落在 4090）不同，兩張卡都符合 8Gi 需求，落點不代表隨機或效能排序。

解除安裝移除了本次建立的 Namespace、10 個 CRD 與以 run-id 命名的 DeviceClass，安裝前已存在的叢集物件保持不變；之後叢集上沒有任何 Kubeflow／Argo CRD。

### 第一次嘗試的問題與處理

第一次（`w3kfp20261012`）的 KFP API server 一直停在 `Initializing DB client...` 而被 startup probe 重啟：套件中 MySQL 與 API server 的密碼設定不一致。解除安裝又因 Argo controller 執行時建立的 `Lease` 不是安裝時留下的物件而拒絕刪除 Namespace，因此以安裝前快照核對後，手動刪除了該 Namespace、10 個新 CRD 與 DeviceClass。套件已修正密碼設定，並讓安裝時建立的 Namespace 不會被執行期產生的物件阻擋刪除；上表即修正後的結果。

## 結論

通過。原生選卡（含 `firstAvailable` 的偏好與替代）、兩卡 HAMi-DRA 的容量選卡，以及 KFP 管線的選卡與 Claim 回收，都在原叢集重現；每次 provider 切換後都以獨立快照核對還原，兩個節點的 HAMi host 路徑都已清除。
