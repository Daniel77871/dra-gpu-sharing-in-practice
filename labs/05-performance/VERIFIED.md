# 實機驗證紀錄：05-performance

本紀錄是在原叢集上實際執行本 lab 縮短版（`SMOKE=1`）的結果：bursty-aligned 工作、N=2、一個 block，GEMM 次數依 `37/367` 縮短（每次等待不變），涵蓋 exclusive、sequential、TimeSlicing 與 MPS。HAMi 模式在下方另列。

## 環境

| 項目 | 值 |
| --- | --- |
| 執行日期 | 2026-10-12（UTC 2026-10-11 17:52–17:54） |
| 執行位置 | 控制平面節點 `dra-m1`，從完整 clone 的儲存庫根目錄執行 |
| Kubernetes | v1.37.0 |
| NVIDIA DRA Driver | 0.5.0；執行期間暫時以 `MPSSupport=true,TimeSlicingSettings=true,ConsumableShares=false` 啟動 A40 的 plugin |
| GPU | `dra-a40`：NVIDIA A40，UUID `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af` |

## 執行的命令

```console
$ cd labs/05-performance
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash scripts/install-native-settings.sh native-perf-20261012-r4
$ bash scripts/verify-native-settings.sh native-perf-20261012-r4
$ SMOKE=1 bash scripts/run.sh perf-smoke-20261012-r4
$ bash scripts/collect.sh perf-smoke-20261012-r4
$ python3 analysis/analyze.py out/perf-smoke-20261012-r4/batches --config out/perf-smoke-20261012-r4/benchmark-config.json --output out/perf-smoke-20261012-r4/reanalysis
$ bash scripts/cleanup.sh perf-smoke-20261012-r4
$ bash scripts/restore-native-settings.sh native-perf-20261012-r4
```

## 結果

四個批次都完成，分析器接受全部 4 個有效批次：

| 模式 | N | 總時間（秒） | Task/s | GPU 使用率 | 平均 Task 延遲（秒） | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| exclusive | 1 | 4.425 | 0.22600 | 32.23% | 4.425 | 1.000 |
| sequential | 2 | 8.838 | 0.22629 | 40.39% | 4.419 | 1.000 |
| TimeSlicing | 2 | 6.472 | 0.30905 | 60.58% | 6.470 | 1.366 |
| MPS | 2 | 4.531 | 0.44141 | 74.19% | 4.502 | 1.951 |

和原始正式結果（bursty-aligned、N=2、五個 block 的平均）對照：

| 指標 | 原始實驗 | 本次縮短版 |
| --- | ---: | ---: |
| TimeSlicing Speedup | 1.361 | 1.366 |
| MPS Speedup | 1.979 | 1.951 |
| MPS／TimeSlicing 吞吐量比 | 1.454 | 1.428 |

縮短版的工作量只有原本的約十分之一、只有一個 block，所以絕對時間不同，但趨勢一致：同步間歇工作在 N=2 時，MPS 接近兩倍吞吐量，且明顯高於 TimeSlicing。

## 過程中修正的問題

前三次嘗試分別暴露三個問題：本機只同步了 `labs/`，缺少 `data/e3/benchmark-config.json`（本 lab 需在完整 clone 中執行）；sequential 與 HAMi 的 kustomize overlay 無法渲染；MPS 批次的身分檢查誤以為 `nvidia-smi` 只會列出 MPS server（Volta 之後也會列出各 client 行程）。修正後即上表的結果。

## provider 設定與還原

每次執行都以 `install-native-settings.sh` 暫時開啟 `TimeSlicingSettings`，只重啟 A40 的 plugin Pod，最後以 `restore-native-settings.sh` 還原。還原後以獨立快照比對原生 DaemonSet：UID、`RollingUpdate`、`FEATURE_GATES=MPSSupport=true,`、node affinity 與 spec 雜湊都和執行前相同，4090 的 plugin 全程沒有重建。

## HAMi 模式

（待 provider 切換窗 2 執行後補上。）

## 清理

結束後叢集上沒有 ResourceClaim，`gpu-performance-lab` Namespace 已刪除，A40 的 compute mode 回到 `Default`。

## 結論

NVIDIA 四種模式通過：復現套件在原叢集重現了第 5 章 bursty-aligned 的主要趨勢。
