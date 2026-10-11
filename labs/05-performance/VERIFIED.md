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

HAMi 批次需要把 A40 切換給 HAMi-DRA，本 lab 使用第 4 章的 `labs/04-isolation/hami-dra/scripts/install-provider.sh` 與 `uninstall-provider.sh`。runner 會在 provider 邊界停下（退出碼 78），切換後以**相同 run-id** 重跑：

```console
$ cd labs/05-performance
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash scripts/install-native-settings.sh native-perf-20261012-hami
$ MODES=hami HAMI=1 SMOKE=1 bash scripts/run.sh perf-hami-smoke-20261012-r5     # NVIDIA 批次，停在 HAMi 邊界
$ bash scripts/restore-native-settings.sh native-perf-20261012-hami
$ (cd ../04-isolation/hami-dra && bash scripts/install-provider.sh hami-provider-20261012-perf2)
$ MODES=hami HAMI=1 SMOKE=1 bash scripts/run.sh perf-hami-smoke-20261012-r5     # HAMi 批次
$ (cd ../04-isolation/hami-dra && bash scripts/uninstall-provider.sh hami-provider-20261012-perf2)
```

同一個 run-id 共完成 4 個有效批次（bursty-aligned，N=2，GEMM 依 `37/367` 縮短）：

| 模式 | N | 總時間（秒） | Task/s | GPU 使用率 | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| exclusive | 1 | 4.408 | 0.22685 | 32.34% | 1.000 |
| sequential | 2 | 8.806 | 0.22712 | 34.02% | 1.000 |
| TimeSlicing | 2 | 6.178 | 0.32371 | 53.46% | 1.425 |
| HAMi | 2 | 5.838 | 0.34259 | 57.46% | 1.508 |

原始正式結果中，bursty-aligned N=2 的 HAMi 與 TimeSlicing 分別為 1.362 與 1.361。本次縮短版只有一個 block、工作量約十分之一，HAMi 略高於 TimeSlicing，但兩者都遠低於上表 MPS 的 1.951，「MPS 明顯領先、TimeSlicing 與 HAMi 相近」的順序與原報告一致；單一短 block 的差距不足以判斷 HAMi 與 TimeSlicing 的高下。

**切換順序要注意：** `install-native-settings.sh` 執行期間，原生 DaemonSet 維持 `OnDelete`；第 4 章的 HAMi 安裝腳本會把這個狀態視為「已被其他流程暫停」而拒絕安裝（這是刻意的安全檢查）。因此要先還原原生設定，再切換到 HAMi；不要把兩種切換疊在一起。第一次嘗試就因為疊用而在任何變更前停止，改用上面的順序後成功。

HAMi 解除安裝後，以獨立快照比對原生 DaemonSet 與執行前相同，HAMi 的 host 路徑已清除。

## 清理

結束後叢集上沒有 ResourceClaim，`gpu-performance-lab` Namespace 已刪除，A40 的 compute mode 回到 `Default`。

## 結論

通過。NVIDIA 四種模式與 HAMi 模式都在原叢集重現了第 5 章 bursty-aligned 的主要趨勢；provider 設定與切換都經獨立核對還原。
