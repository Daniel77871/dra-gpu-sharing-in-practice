# 一般 Job 的 GPU 選擇

`native/` 使用原生 NVIDIA provider 驗證型號、架構、任意 GPU、無匹配需求與優先替代清單；`capacity/` 使用兩張卡上的 HAMi-DRA 驗證 memory／cores 的可累加需求。所有 worker 與 holder 都使用 ResourceClaimTemplate，Kubernetes 為每個 Job Pod 建立獨立 Claim。

請從完整 clone 的儲存庫根目錄執行[實驗總說明](../README.md)列出的 run、collect、analysis 與 cleanup 命令。`scripts/control.py` 從一份 native 或 capacity Job 複製不同角色，改寫物件名稱、component label、task-id 與 ClaimTemplate reference；task-id 包含情境與輪次，避免上一輪的控制檔投影誤放行新 Pod。完整實際輸入保存在 `out/<run-id>/manifests.json` 及每個 case 的 `workers.json`／`holders.json`。

## Manifest 分組

| 檔案 | 用途 |
|---|---|
| `common/namespace.yaml` | 本實驗專用 Namespace |
| `common/kustomization.yaml` | 從 `workload/` 建立程式 ConfigMap，以及可投影更新的控制 ConfigMap |
| `native/claims.yaml` | 六種 native 需求的 ResourceClaimTemplate |
| `native/jobs.yaml` | 原生 Job 的共同範本 |
| `examples/prefer-4090.yaml` | 優先 RTX 4090、其次 A40 的 Template，納入 native kustomization |
| `capacity/claims.yaml` | 無型號 selector 的 8 GiB 與 30 GiB 需求 |
| `capacity/holders.yaml` | 三個 holder 的 ResourceClaimTemplate |
| `capacity/jobs.yaml` | Capacity worker 與 holder 的共同 Job 範本 |

`kustomization.yaml` 都可獨立渲染；執行情境由 `PART` 與 `CASES` 選擇，再分步提交，避免一次啟動全部工作。單純 `kubectl apply -k` 只會啟動檔案中的代表 Job，無法完成 holder 控制、Pending 檢查與 UID 比較，請用總說明的 run 腳本。

## 優先替代清單與延伸範例

`prefer-4090` 先在兩張 GPU 空閒時提交工作，再使用普通獨占 Template 建立 RTX 4090 holder，於 holder 持有配置時以相同優先 Template 提交工作。兩步分別驗收 `gpu/rtx4090`、`gpu/a40`，worker Pod 沒有固定節點；holder 在第二步完成後釋放。完整輸入與配置子請求保存於每個 case 的 phase 子目錄及 `trial.json`；原叢集的實測結果見[實機驗證紀錄](../VERIFIED.md#原生選卡不切換-provider)。

`examples/attribute-requirements.yaml` 示範型號、memory 與 CUDA compute capability 的交集，沒有納入執行情境。多屬性 CEL 與 `firstAvailable` 都未在原 E4／E4B 執行；適用版本、feature gate 與型別說明見[需求表示方式](../../../book/06-heterogeneous/selection.md)。
