# 實機驗證紀錄：03-oom

本紀錄是在原叢集上實際執行本 lab 縮短版（`SMOKE=1`：hold 模式一輪）的結果。

## 環境

| 項目 | 值 |
| --- | --- |
| 執行日期 | 2026-10-11（UTC 12:38–12:42） |
| 執行位置 | 控制平面節點 `dra-m1`（kubectl v1.37.0，內建 kustomize） |
| Kubernetes | v1.37.0 |
| NVIDIA DRA Driver | 0.5.0（原生 provider，未切換） |
| GPU | `dra-a40`：NVIDIA A40，UUID `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af` |

## 執行的命令

```console
$ cd labs/03-oom
$ SMOKE=1 bash scripts/run.sh smoke-20261011
$ bash scripts/cleanup.sh smoke-20261011
```

## 結果

`run.sh` 結束碼為 0。A、B 的 `READY` 事件都回報同一張 A40（相同 UUID），`claim.json` 的 `reservedFor` 同時列出兩個 Pod。

```text
| 輪次 | B 張量 MiB | hold 探測 OOM | hold 探測 OK | 恢復秒數 | A 運算錯誤 | A 退出碼 | B 退出碼 | hold p95 倍率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hold-r1 | 43776 | 30 | 0 | 0.847 | 0 | 0 | 1 | 0.994 |
```

和原始實驗的 hold 三輪對照：

| 指標 | 原始實驗（hold-r1～r3） | 本次縮短版 |
| --- | --- | --- |
| B 在 OOM 時保留的張量 | 43,776 MiB（171 塊） | 43,776 MiB |
| hold 期間 A 的新配置 | OOM 30／30／29 次，成功 0 次 | OOM 30 次，成功 0 次 |
| B 退出後首次配置成功 | 0.525／0.741／0.836 秒 | 0.847 秒 |
| A 的運算錯誤與退出碼 | 0；A 0、B 1 | 0；A 0、B 1 |
| hold p95 延遲倍率 | 0.993／0.961／1.002 | 0.994 |

趨勢與原始實驗一致：B 保留 VRAM 期間，A 的既有運算沒有錯誤，但每一次新配置都 CUDA OOM；B 退出後不到一秒就恢復成功。恢復秒數是觀察值，包含控制與探測延遲，不是釋放本身的耗時。

## 觀察到的小問題

- `run.sh` 在容器尚未啟動時就嘗試讀取日誌，輸出兩行 `Error from server (BadRequest): ... ContainerCreating`。腳本會重試，不影響結果。
- `cleanup.sh` 刪除了所有帶標籤的物件，但留下空的 `gpu-oom-lab` Namespace；這次手動刪除。

## 清理

清理後叢集上沒有 ResourceClaim，`gpu-oom-lab` Namespace 已刪除，A40 的 `memory.used` 回到 0 MiB。

## 結論

通過。復現套件在原叢集重現了第 3 章的核心結果。

## 修正後重跑

兩個小問題修正後（`cleanup.sh` 會刪除本書標籤的 Namespace；`run.sh` 等容器啟動後才讀日誌），同日以 `SMOKE=1 bash scripts/run.sh smoke-20261011-r2` 再跑一次：

```text
| hold-r1 | 43776 | 30 | 0 | 0.233 | 0 | 0 | 1 | 0.983 |
```

結果與第一次一致（恢復秒數為觀察值，兩次分別為 0.847 與 0.233 秒）。這次沒有 `BadRequest` 訊息，`cleanup.sh` 刪除了 `gpu-oom-lab` Namespace，叢集上沒有殘留 ResourceClaim。
