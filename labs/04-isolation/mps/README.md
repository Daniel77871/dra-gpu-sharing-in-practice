# MPS 每個 CUDA 行程的配額

本套件讓 `steady-service`（A）與 `quota-pressure`（B）共用 `a40-shared-mps` Claim，由 NVIDIA DRA 建立 MPS 服務。單行程測試檢查 4 GiB 與 8 GiB 配額邊界，雙行程測試檢查同一 Pod 的合計用量；背景與完整 YAML 見[第 4.2 節](../../../book/04-isolation/mps.md)。

## 前置條件與執行

依[共用前置條件](../README.md#前置條件)準備原生 NVIDIA DRA v0.5.0 provider，`MPSSupport=true` 必須已啟用，ConsumableShares provider 必須已移除並還原。MPS 不執行 `provider/` 的切換腳本，單一 A40 publisher 與 GPU 閒置檢查通過後才建立 Claim。

從儲存庫根目錄執行以下命令。未指定 `CASE` 的完整模式執行全部三個案例，各三輪；指定 `CASE=single-4g|single-8g|dual-8g` 可選一個案例。

```sh
$ bash labs/04-isolation/mps/scripts/run.sh mps-full
$ SMOKE=1 bash labs/04-isolation/mps/scripts/run.sh mps-smoke
$ CASE=single-4g SMOKE=1 bash labs/04-isolation/mps/scripts/run.sh mps-4g-smoke
$ bash labs/04-isolation/mps/scripts/collect.sh mps-smoke
$ python3 labs/04-isolation/common/analysis/analyze.py labs/04-isolation/out/mps-smoke --output labs/04-isolation/out/mps-smoke
$ bash labs/04-isolation/mps/scripts/cleanup.sh mps-smoke
```

第一、二、三行是各自獨立的執行選項，不須連續執行。`SMOKE=1` 預設執行 `single-8g` 與 `dual-8g` 各一輪；顯式 `CASE` 則只執行該案例一輪。縮短模式只減少重複次數，仍保留至少 60 秒 baseline、30 秒共同 hold 與 B 真正退出後至少 60 秒 recovery。

## 輸入與判讀

`manifests/01-shared-claim.yaml` 使用 `GpuConfig.sharing.strategy: MPS`，`defaultActiveThreadPercentage: 100`、`defaultPinnedDeviceMemoryLimit: 8Gi`、`multiUser: false`。所有 CUDA client 都以 UID／GID 0 執行；A、B 的 Pod 都引用同一個 Claim，沒有 `capacity.requests`。`single-4g/kustomization.yaml` 同時改 Claim limit 與 A／B 的 `--quota-gib` 為 4，其餘環境變數、映像檔與配置路徑相同。

兩個 B YAML 使用同一個 Job 名稱，是互斥選項；腳本逐輪分階段套用，先確認 A、B 的 CUDA 身分，再讓 B 開始壓力。工作負載由共用 ConfigMap generator 掛載，使用固定映像檔 `docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`。

| 案例 | MPS 每個 client 配額 GiB | 原實驗 B 成功張量 GiB | 原實驗 outcome | 共同 hold 中 A |
|---|---:|---:|---|---|
| `single-4g` | 4 | 3.5 | `allocation_oom` | 每輪 30 次成功，0 OOM |
| `single-8g` | 8 | 7.5 | `allocation_oom` | 每輪 30 次成功，0 OOM |
| `dual-8g` | 8 | 5 + 5 = 10 | `target_reached` | 每輪 30 次成功，0 OOM |

上述數值是[原始九輪摘要](../../../data/e2/runtime.csv)，不是新叢集的保證值。`defaultPinnedDeviceMemoryLimit` 限制每個 CUDA client，CUDA 內部配置也計入限制，所以成功張量量可以低於宣告值；雙行程案例的合計 10 GiB 不代表一個 client 超過 8 GiB。

`out/<run-id>/` 保存每輪輸入、A／B 日誌、Claim allocation、MPS daemon 物件、終止狀態、500 ms 整卡取樣與摘要。NVML 的整卡峰值包含 A、B、MPS 服務與 driver 負擔，不可直接當作 B Pod 用量。判定必須同時看 `b_outcome`、兩個 client 的共同 hold、A compute errors、正常退出與清理驗證；退出碼 0 不會把配額 OOM 改判為沒有 OOM。

## 收集與清理

各輪自動先收集再清理，移除 Claim 後等待 compute mode 回到 `Default`，MPS daemon 由原生 driver 釋放，最後才印出本輪判定。中斷時先重跑 `collect.sh <run-id>` 保留當前證據，再執行 `cleanup.sh <run-id>`；它會清理本次工作及帶有 dra-book 標籤的 `gpu-isolation-lab` Namespace，並等待刪除完成。若 Namespace 仍有外來物件，或正常控制無法停止 CUDA 工作，腳本會停止並留下紀錄。

原始叢集縮短版的單行程案例已完成，真實輸出見[第 4.2 節](../../../book/04-isolation/mps.md)；雙行程修正後仍須重新執行，完整實機驗證紀錄尚未建立。
