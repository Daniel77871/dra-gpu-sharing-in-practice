# GPU 記憶體隔離復現套件

本套件分開驗證排程記帳、CUDA 配置配額與 Pod 的實體 VRAM 用量。共用設計見[第 4 章](../../book/04-isolation/README.md)，兩個可執行入口是 [ConsumableShares](consumable-shares/README.md) 與 [MPS](mps/README.md)；工作負載、收集與分析共用 `common/`，provider 切換另放在 `provider/`。

> **實驗叢集限定：ConsumableShares 的安裝與還原會修改既有 NVIDIA DaemonSet、刪除 A40 plugin Pod 與 ResourceSlice。先停止目標 GPU 的工作並清理 Claims，保留備份到還原核對完成。勿在正式服務節點執行。**

## 前置條件

先完成[第 2 章](../../book/02-deploy/README.md)。原始環境是 Kubernetes v1.37.0、NVIDIA DRA v0.5.0、GPU Operator v26.7.0、NVIDIA driver 595.91.07；PyTorch 2.5.1+cu124 與 CUDA runtime 12.4 在固定映像檔中執行。控制端需要 Bash、Python 3 與含 Kustomize 的 `kubectl`，分析只使用 Python 標準函式庫；請使用儲存庫的完整 clone，保留共用工具與 `data/` 的原始結果。

本套件固定選擇 `dra-a40` 的 `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af`，同一時間只能有一個 provider 公布它。`gpu.nvidia.com` DeviceClass 不得附加設定；`gpu-isolation-lab` 若已存在，必須有 `app.kubernetes.io/part-of: dra-book`，且不能留下前一輪 Jobs、Pods 或 Claims。移植到另一個叢集時，先一致修改各 Claim 的 UUID、provider 節點限制與 `common/analysis/control.py` 的身分檢查，再確認原始 provider 的名稱、RBAC 與 affinity 適用。

需要讀取 Claims、Slices、Pods、events、DeviceClass、版本、Jobs 與 NVIDIA Namespace 的 Deployments／ReplicaSets，以及建立本實驗 Namespace、ConfigMap、Claims、Jobs 和 `pods/exec` 的權限。provider 操作另外需要讀取原始 ServiceAccount／RBAC、修改 NVIDIA DaemonSet、以 UID 前置條件刪除 plugin Pod 和目標 Slice 的權限。GPU Operator driver Pod 必須在目標節點 Ready，driver 容器名稱沿用 `nvidia-driver-ctr`。

## 執行順序

MPS 使用第 2 章已啟用 `MPSSupport` 的原生 NVIDIA provider，不切換 provider。先完成 MPS，再安裝 ConsumableShares；ConsumableShares 執行結束，清理工作負載、移除替代 provider 並還原原始 DaemonSet。兩種 provider 設定不可同時公布 A40。

以下從儲存庫根目錄執行，run-id 為 1 至 63 字元，只含英數字、點、底線或連字號，首尾須為英數字。`OUT_DIR` 會由工作負載入口依 run-id 設定成 `labs/04-isolation/out/<run-id>/`，不要將 provider 備份留在另一個目錄。

```sh
$ SMOKE=1 bash labs/04-isolation/mps/scripts/run.sh mps-smoke
$ bash labs/04-isolation/mps/scripts/collect.sh mps-smoke
$ python3 labs/04-isolation/common/analysis/analyze.py labs/04-isolation/out/mps-smoke --output labs/04-isolation/out/mps-smoke
$ bash labs/04-isolation/mps/scripts/cleanup.sh mps-smoke
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash labs/04-isolation/consumable-shares/scripts/install-provider.sh capacity-smoke
$ SMOKE=1 bash labs/04-isolation/consumable-shares/scripts/run.sh capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/collect.sh capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/cleanup.sh capacity-smoke
$ bash labs/04-isolation/consumable-shares/scripts/uninstall-provider.sh capacity-smoke
```

Full mode 使用各條件三輪：MPS 的 `single-4g`、`single-8g`、`dual-8g`，以及 ConsumableShares 的 `accounting`、`runtime`。`SMOKE=1` 只減少重複次數：MPS 預設選 8 GiB 單行程與雙行程各一輪，ConsumableShares 選記帳與執行期各一輪；60 秒 baseline、30 秒 hold、60 秒 recovery 都保留。明確指定 `CASE` 或 `MODE` 時，只執行該條件一輪。

## 共用工作負載與資料

`common/workload/kustomization.yaml` 產生唯一的 `isolation-code` ConfigMap。A 常駐 2 GiB，每秒運算並探測新的 512 MiB；B 以 256 MiB 區塊增加張量，每次等待 0.2 秒。真正 CUDA 配置 OOM 後，B 同步並保留成功張量 30 秒；單行程邊界測試的成功張量超過本輪配額時，B 記錄 `B_LIMIT_EXCEEDED`、同步退出，結果為 `limit_not_enforced`，不再壓滿整張 GPU。PyTorch GPU 記憶體快取已停用，沒有設定 memory fraction。

雙行程 B 的 parent 不建立 CUDA context，兩個 child 各以 5 GiB 為目標，安全配額檢查仍在各 child 內；合計 10 GiB 用來檢查 Pod 範圍，沒有合計超過 8 GiB 就自動停止的邏輯。記帳工作負載只執行 `nvidia-smi -L; sleep 3600`，讓 Pod 觸發 Claim allocation，沒有常駐 CUDA 張量。這些 Python 來源沿用原實驗邏輯；Job、命名、分階段控制與輸出目錄是復現包裝。

執行期由控制端送入帶有唯一 `command_id` 的控制檔，收到 A 的 `CONTROL_ACK` 才開始計時。B 容器真正退出後才計算 recovery。每輪收集完成才清理 Jobs、等待 Pods 消失、刪除 Claims，並確認 GPU 回到 `Default`、沒有 compute 行程及 A40 的 MPS daemon Pods／Deployment，記憶體用量最多 64 MiB；這個門檻與原實驗的清理及 provider 切換一致。

GPU 取樣在 GPU Operator driver Pod 執行 `nvidia-smi --query-gpu=timestamp,uuid,memory.total,memory.used,memory.free,utilization.gpu,compute_mode --format=csv -lms 500`，保存 UTC `gpu.csv`。取樣器會核對 worker 身分與 heartbeat，停止時只處理本次取樣行程；不需要 SSH。完整命令見 [sample.sh](common/scripts/sample.sh)，時間窗口與重算規則見[資料說明](../../data/e2/README.md)。

各輪保留 `manifests.yaml/json`、分階段輸入、`trial.json`、A／B 日誌、Claims、events、Pod 終止狀態、GPU 取樣與清理證據。分析產生 `summary.json` 和 `summary.csv`，包含 B 最大成功張量、outcome、quota、共同 hold 中 A 的成功／OOM 次數、compute errors、退出碼與 B 活躍期間整卡 NVML 峰值。`complete=false` 的列不算成功；分析退出碼 2 表示缺少完整證據。中途收集只更新摘要，runner 在每輪清理驗證完成後才印出一次最終判定。

## 中斷與清理

`run.sh` 中斷或錯誤時會停止本次取樣、收集目前證據並保留工作負載，讓結果可檢查。先執行 `collect.sh <run-id>`，再執行 `cleanup.sh <run-id>`；清理會要求本輪 CUDA 工作正常同步停止，遇到不可確認的 Pod UID 或取樣身分便拒絕刪除。分階段輸入會加上 `app.kubernetes.io/instance: <run-id>`，清理先核對目前物件屬於選定執行，再只刪除同時有 `part-of: dra-book` 和該 instance 的 Jobs、Claims 與工作負載 ConfigMap。最後檢查 Namespace 內各類物件，確認沒有外來物件或其他執行的工作，才刪除帶有 dra-book 標籤的 `gpu-isolation-lab` 並等待刪除完成；provider 與備份仍保留。

完成的 run-id 再次執行只重算摘要，不重跑；已開始但未完成的 run-id 不會覆寫，清理後用新的 run-id。provider 的重入與還原方式見 [provider 說明](provider/README.md)，備份與原始 UID／spec 核對不可略過。

原始叢集的縮短版已完成 MPS 單行程案例；雙行程與 provider 切換修正後仍須重新執行，完整實機驗證紀錄尚未建立。書中目前的原始量測資料見 [data/e2](../../data/e2/README.md)，MPS 單行程的縮短版輸出另見[第 4.2 節](../../book/04-isolation/mps.md)。
