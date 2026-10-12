# 實機驗證紀錄：04-isolation

本紀錄是在原叢集上實際執行本 lab 縮短版（`SMOKE=1`）的結果。MPS 使用原生 NVIDIA provider；ConsumableShares 需要暫時把 A40 切換給替代 provider，跑完再還原。HAMi-DRA 的部分在下方另列。

## 環境

| 項目 | 值 |
| --- | --- |
| 執行日期 | 2026-10-12（UTC 2026-10-11 17:04–17:51） |
| 執行位置 | 控制平面節點 `dra-m1`，從完整 clone 的儲存庫根目錄執行 |
| Kubernetes | v1.37.0 |
| NVIDIA DRA Driver | 0.5.0（原生 provider，`FEATURE_GATES=MPSSupport=true`） |
| GPU | `dra-a40`：NVIDIA A40，UUID `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af` |

## MPS

```console
$ SMOKE=1 bash labs/04-isolation/mps/scripts/run.sh mps-smoke-20261012-r2
$ bash labs/04-isolation/mps/scripts/cleanup.sh mps-smoke-20261012-r2
```

| 案例 | B 的結果 | B 成功 tensor | A 在共同保留期的 probe | 整卡 NVML 峰值 |
| --- | --- | --- | --- | --- |
| single-process，每 client 8 GiB | CUDA 配置 OOM | 7.5 GiB | 成功 30、OOM 0 | 10,379 MiB |
| 同一 Pod 內的 dual-process，每 client 8 GiB | 兩個 client 各達 5 GiB 目標 | 合計 10.0 GiB | 成功 30、OOM 0 | 13,246 MiB |

結果與原始實驗一致：MPS 的上限套用在每個 CUDA client，單一 process 在 8 GiB 之前就配置失敗，但同一個 Pod 的兩個 process 合計保留了 10 GiB；A 在 B 保留期間持續可以配置與運算。兩個案例的 NVML 峰值也與原始實驗相同。

第一次執行（`mps-smoke-20261012`）時，dual-process 案例因為 process 身分對照的程式錯誤而中止；修正後的套件即上表的結果。

## ConsumableShares（provider 切換窗）

```console
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash labs/04-isolation/consumable-shares/scripts/install-provider.sh capacity-smoke-20261012-r2
$ SMOKE=1 bash labs/04-isolation/consumable-shares/scripts/run.sh capacity-smoke-20261012-r2
$ bash labs/04-isolation/consumable-shares/scripts/collect.sh capacity-smoke-20261012-r2
$ bash labs/04-isolation/consumable-shares/scripts/cleanup.sh capacity-smoke-20261012-r2
$ bash labs/04-isolation/consumable-shares/scripts/uninstall-provider.sh capacity-smoke-20261012-r2
```

- 記帳：16 GiB 與 24 GiB 的 Claim 配置後，8 GiB 的第三份 Claim 維持 Pending，scheduler 事件與原始實驗相同：

  ```text
  0/4 nodes are available: 1 node(s) had untolerated taint(s), 3 cannot allocate all claims. preemption: 0/4 nodes are available: 4 Preemption is not helpful for scheduling.
  ```

  釋放 24 GiB 的工作後，第三份 Claim 取得配置。
- Runtime：B 的 Claim 申請 8 GiB，但 CUDA 成功保留到 8.25 GiB，工具依安全規則停止（`limit_not_enforced`），期間整卡 NVML 峰值為 10,866 MiB。A 在壓力期間 10 次 probe 全部成功。這重現了「ConsumableShares 只做排程記帳，沒有 runtime 配額」的結論。

### Provider 切換與還原

切換前後都以獨立快照比對原生 DaemonSet（`nvidia-dra-driver-gpu/dra-driver-nvidia-gpu-kubelet-plugin`）：UID、`updateStrategy: RollingUpdate`、`FEATURE_GATES`、5 個 node affinity terms 與 spec 雜湊在還原後都和切換前相同；4090 的 plugin Pod 全程沒有重建，A40 的 ResourceSlice 重新公布。

第一次切換（`capacity-smoke-20261012`）在刪除 A40 plugin Pod 時遇到競態：DaemonSet controller 先開始刪除該 Pod，使 resourceVersion 前置條件失效，安裝腳本中止；當時替代 provider 尚未安裝。還原腳本在舊 Pod 結束後完成還原並通過上述比對。修正後的腳本會把這種情況視為「已由 controller 刪除」，上表即修正後的結果。

## HAMi-DRA（provider 切換窗 2）

```console
$ cd labs/04-isolation/hami-dra
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash scripts/install-provider.sh hami-provider-20261012-r2
$ MODE=accounting SMOKE=1 bash scripts/run.sh hami-acct-smoke-20261012-r2
$ SMOKE=1 bash scripts/run.sh hami-smoke-20261012-r2
$ bash scripts/uninstall-provider.sh hami-provider-20261012-r2
```

安裝後，A40 由 `a40-hami-provider` 公布 memory 與 cores 容量，並建立 DeviceClass `hami-core-gpu.project-hami.io`。結果與原始實驗逐項相同：

| 案例 | 本次結果 | 原始實驗 |
| --- | --- | --- |
| 記帳（memory 與 cores） | 第三份 Claim Pending，scheduler 事件與原始相同；公布 100 cores，已保留 20、可排程 80 | 相同 |
| single-process，Claim 8 GiB | 7.5 GiB 後 CUDA OOM；B 的 NVML 實體量 7,986 MiB；A 共同保留期成功 30、OOM 0 | 7.5 GiB；7,986 MiB |
| 同一 container 內的 dual-process，Claim 8 GiB | 兩個 process 各 3.75 GiB（合計 7.5 GiB），2 次 CUDA OOM；B 的 NVML 合計 8,292 MiB，超過 8,192 MiB | 7.25–7.5 GiB；8,292 MiB |
| 兩個 Pod 共用同一 Claim（E2B case4a） | 6.0 + 3.75 = 9.75 GiB，超過 8 GiB；NVML 合計 10,596 MiB；兩個 process 使用同一個 cache | 9.75 GiB；10,596 MiB |

這重現了第 4.3 節的兩個關鍵結論：HAMi-DRA 的配額在共用 cache 的配置路徑上生效，但實體用量仍可能超過申請量；兩個 Pod 共用同一份 Claim 時，合計配置可以超過配額。

### 切換過程中遇到的問題與處理

- 第一次安裝（`hami-provider-20261012`）在任何變更前就停止：`kubectl get -f <檔案> --ignore-not-found -o json` 在物件都不存在時輸出空字串（不是空的 List），腳本無法解析。已修正為統一的 `kc_get_list` 輔助函式並加上測試。
- 第二次的解除安裝移除了 HAMi provider、DeviceClass 與 ResourceSlice，但 host 清理步驟遇到 plugin 執行時建立的 `pu.lock`，依安全規則拒絕刪除未知檔案，因此在還原原生 provider 之前中止。處理方式：以同一份備份執行 `labs/04-isolation/provider/restore.sh`，原生 provider 還原並通過獨立快照比對（UID、`RollingUpdate`、`FEATURE_GATES`、affinity、spec 雜湊）；再以 `kubectl debug node` 確認安裝前的 host 快照中 `/usr/local/vgpu`、`/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io`、`/tmp/vgpulock` 都不存在，才刪除這三個路徑。`/var/run/cdi` 與 kubelet 的 plugin registry 和安裝前相同，host 的 `/etc/ld.so.preload` 沒有被修改。


## 清理

兩部分結束後，叢集上沒有 ResourceClaim，`gpu-isolation-lab` Namespace 已刪除，A40 的 compute mode 回到 `Default`，沒有殘留的 MPS daemon。

## 結論

通過。MPS、ConsumableShares 與 HAMi-DRA 三部分都在原叢集重現了第 4 章的對應結果；provider 切換與還原經獨立核對，HAMi 的 host 路徑已確認清除。
