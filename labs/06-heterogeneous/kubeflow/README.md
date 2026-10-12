# KFP Task 與 GPU 容量選擇

這個套件以 standalone Kubeflow Pipelines 執行 H1、H2 與 L1，讓 Task 透過 ResourceClaimTemplate 取得 GPU。H1 只要求 8Gi GPU 記憶體與 25 cores，H2 改成 30Gi；L1 在完成同一份 CUDA 正確性檢查後，以應用程式 exit code 42 受控失敗。背景與原始結果見[第六章 KFP 整合](../../../book/06-heterogeneous/kubeflow.md)。

## 前置條件

從完整 clone 的根目錄執行下列命令。控制端需要 Bash、Python 3、kubectl 與支援 `--fail-with-body` 的 curl；提交已編譯的 pipeline 不需要安裝 KFP SDK。叢集需要 Kubernetes v1.37 的 consumable capacity、`local-path` StorageClass 與 `Delete` 回收政策，並提供名稱為 `dra-non` 的一般節點、`dra-a40` 與 `dra-4090` 兩個 GPU 節點。

兩個 GPU 節點都必須已由 HAMi-DRA 發布可多重配置、包含 memory 與 cores 的 ResourceSlice。進入與還原 provider 的步驟見[容量選擇套件](../selection/README.md)及[provider 操作](../../04-isolation/provider/README.md)；KFP 指令只檢查這項前置條件。每次 trial 前必須釋放既有 GPU Claim，避免把背景配置誤判為容量選擇結果。L1 保留原始 A40 UUID selector；換到自己的叢集時，設定 `A40_GPU_UUID` 為該節點實際發布的 UUID。

## 安裝後端

**警告：安裝會建立叢集範圍的 CRD 與 DeviceClass；解除安裝會刪除本次 Namespace 及其資料。請保留原始 KFP_STATE_DIR，並先收集結果。** 同一份安裝只管理一個新 Namespace，名稱是 `gpu-kubeflow-<run-id>-lab`。既有 Namespace 不會被接管，既有 CRD 不會被覆寫；不相容時指令會停止並保留現況。

安裝來源是[本地 kustomization](install/kustomization.yaml)及[完整物件清單](install/objects.json)，沒有遠端資源。它保留原始 standalone 後端的 ServiceAccount、Role、RoleBinding、Service、ConfigMap、Deployment、初始化 Job 與 PVC；一般後端及 CPU driver 排在 dra-non，GPU Task 的 claim 沒有節點固定設定。所有 image 都使用[固定 tag 與 digest](install/images.json)，憑證在安裝時產生並交由 Namespace 內的 Secret 保存。

MySQL 的 `MYSQL_ROOT_PASSWORD` 與 API 的 `DBCONFIG_PASSWORD`、`DBCONFIG_MYSQLCONFIG_PASSWORD` 都引用同一個 `mysql-secret/password`，讓資料庫初始化與 API 登入使用同一份產生的憑證。MySQL 已有資料目錄時不會重新套用初始化密碼，因此修正設定後請先解除舊安裝，再使用新的 `KFP_RUN_ID`、Namespace 與 PVC；不要把新的環境變數當成既有資料庫的密碼更新方式。[初始化行為與設定來源](install/SOURCES.md#資料庫初始化與診斷)

```bash
$ export KFP_RUN_ID="$(date -u +%Y%m%d%H%M%S)"
$ export KFP_STATE_DIR="$PWD/labs/06-heterogeneous/kubeflow/out/$KFP_RUN_ID/kfp-install"
$ ALLOW_KFP_INSTALL=1 bash labs/06-heterogeneous/kubeflow/scripts/install-kfp.sh
```

| 參數 | 意義 | 為什麼這樣設 |
|---|---|---|
| `KFP_RUN_ID` | 此次安裝的識別碼 | 使用不超過 40 字元的 DNS label，區分配置與結果 |
| `KFP_STATE_DIR` | 安裝前快照與 UID 紀錄的目錄 | 解除安裝必須讀取同一份紀錄 |
| `ALLOW_KFP_INSTALL=1` | 明確啟用安裝 | 將叢集範圍變更與提交 Task 分開 |
| `KFP_NAMESPACE` | 可選的新 Namespace | 預設依 run-id 產生；只能使用 `gpu-kubeflow-…-lab` |
| PVC 5Gi 與 `local-path` | MySQL 與 SeaweedFS 的儲存空間 | 保留原始後端設定，便於隨 Namespace 回收 |

安裝前會將 CRD、ClusterRole、ClusterRoleBinding、MutatingWebhookConfiguration 與 ValidatingWebhookConfiguration 的現況存入 `cluster-before.json`，並保存原始 NVIDIA DaemonSet 的 UID 與 spec。這份 namespaced standalone bundle 建立 10 個 CRD，沒有建立 ClusterRole、ClusterRoleBinding 或 webhook。另建立一個以 run-id 命名的 HAMi DeviceClass；它只能選取 `hami-core-gpu.project-hami.io` 發布的 GPU。

安裝失敗時，指令仍回報原始錯誤，並將診斷存到 `$KFP_STATE_DIR/diagnostics/<UTC時間>/`；同一安裝重試會保留不同次的診斷。內容包含 API server 與 MySQL 的目前及前一次 container logs、已開始執行的 init-container logs、Pod／Deployment 狀態、Service、EndpointSlice 與事件。`report.json` 列出每個命令的結果、收集失敗原因與未就緒的 container；ContainerCreating 或 PodInitializing 的目前 container 不會呼叫 logs，沒有前一次執行紀錄時也不呼叫 `--previous`。診斷不查詢 Secret 值，且個別命令失敗或逾時不會中斷其他證據的收集。

## 執行與收集

```bash
$ SMOKE=1 bash labs/06-heterogeneous/kubeflow/scripts/run.sh
$ bash labs/06-heterogeneous/kubeflow/scripts/collect.sh
```

`SMOKE=1` 依序執行 H1、H2、L1 各一次。完整模式預設各跑三輪，可用 `REPEATS` 與 `CASES=h1,h2,l1` 調整；兩個模式都保留原始 4096×4096 FP32 GEMM、seed 20261010、warmup 5 次、測量 20 次、TF32 關閉與 8 個正確性取樣點。此套件重現容量選擇與 Claim 生命週期，結果不代表 scheduler 選中了最快的 GPU。

`run.sh` 透過 kubectl 將 ml-pipeline Service 轉送到 localhost，並在 GPU Operator 的 driver Pod 內，以 500 ms 間隔執行 nvidia-smi。`submit.sh` 呼叫 stdlib helper，以 curl 上傳 committed IR、建立 Experiment 與 Run、等待 Run 終止，並從啟動前的 resourceVersion 監看 Pod 與 ResourceClaim。Pod watch 一看到 main container 執行便開始串流 logs，保留短暫失敗 Task 的輸出；watch 提早結束時會停止驗收。每輪使用不同的 Task 控制識別碼，避免前一輪尚未更新的 ConfigMap 投影提前釋放工作。

H1、H2 的 READY、CORRECT 與 HOLD 必須來自本輪 Pod UID，且 CUDA UUID 必須對應 Claim 配置的 ResourceSlice device，才會發出釋放命令。L1 使用同樣的身分檢查並走失敗路徑；同名且 UID 未變的 workload ConfigMap 或 ClaimTemplate 若與本地輸入不同，也會停止並要求使用新的 run-id。

| 情境 | 需求 | 驗收重點 |
|---|---|---|
| H1 | 任意 HAMi GPU，8Gi、25 cores | 任一合格 GPU 完成 CUDA 檢查，Run SUCCEEDED，Claim 自動刪除 |
| H2 | 任意 HAMi GPU，30Gi、25 cores | 在原始容量下只有 A40 符合，Run SUCCEEDED，Claim 自動刪除 |
| L1 | A40 UUID，8Gi、25 cores | CUDA 正確、應用程式回報 42，Run FAILED，Claim 自動刪除 |

KFP launcher 的 main container exit code 可能與應用程式的 42 不同，兩者分別保存為 `main_exit_codes` 與 `exit_codes`。若只看到 Run FAILED 而沒有 CUDA 正確性、`CONTROLLED_FAILURE` 與 `WORKLOAD_EXIT` 事件，該輪會標為 FAIL。套件依本輪 Pod owner UID 分類所有觀察到的產生 Claim，分別保存完整 UID 清單與曾配置的 UID 清單；每個產生 UID 都必須有 DELETED watch 事件，且終態清單為空，才算自然釋放。Namespace 清理不會被計入這項證據。

結果寫入 `kubeflow/out/<run-id>/kubeflow/<case>-r<round>/`。`trial.json` 統一記錄落點、Pod／Claim UID、run_state、exit code 與 claims_deleted；原始 REST 回應、watch JSONL、Pod logs、allocation 與刪除紀錄會保留在同一目錄。`collect.sh` 收集終態並呼叫[stdlib 分析](../analysis/analyze.py)，輸出 `summary.json`。單獨提交時，先自行執行 port-forward，再設定同一份安裝紀錄：

```bash
$ kubectl -n "gpu-kubeflow-$KFP_RUN_ID-lab" port-forward --address=127.0.0.1 service/ml-pipeline 8888:8888
# 在另一個命令列視窗，沿用 KFP_STATE_DIR
$ bash labs/06-heterogeneous/kubeflow/scripts/submit.sh h1 1
```

## 解除安裝與驗證

```bash
$ ALLOW_KFP_UNINSTALL=1 bash labs/06-heterogeneous/kubeflow/scripts/uninstall-kfp.sh
```

清理先核對安裝器建立的 Namespace UID、dra-book label 與 run-id，也核對已記錄的 namespaced 物件 UID。這個專用 Namespace 的 runtime 內容一併清除，包含尚未記錄的 Argo Lease、Events、Pods、Workflows，以及 controller 產生的 ConfigMap 或 Secret；它們不需要另有歸屬 label 或 ownerReferences。刪除使用 Kubernetes DeleteOptions 的 UID 與 resourceVersion 前置條件，避免刪除同名替代的 Namespace 或叢集物件。

Namespace 刪除後會等待其消失，確認本次 PVC 對應的 PV 依 `Delete` 政策回收，再逐一處理本次新增的 DeviceClass 與 CRD。即使安裝未完成，或前一步清理失敗，仍會嘗試其他自有叢集物件；有外來實例或 Claim／Template 引用的物件會保留，其他物件繼續清理。只在重新確認 Namespace UID 相符時，才把其中的引用視為本次安裝的內容；同名替代 Namespace 的引用仍受保護。

所有安裝前存在的叢集範圍物件都需維持原始 UID 與設定；原始 NVIDIA DaemonSet 的 UID 與 spec 也須一致。結果與各步錯誤保存為 `uninstall-verification.json`，部分失敗會回傳非零 exit code 且 `verified` 為 false；用同一份 `KFP_STATE_DIR` 重試，已刪除的物件會略過。`storage-before.json` 保留各次清理的 PV UID，即使 PVC 已消失，也會繼續確認原 PV 已回收；同名 PV 的 UID 不同時會保留現況並回報失敗。重跑已成功的清理是安全的；不要刪除 state 目錄來跳過檢查。

## 重新建立 SDK 與編譯

直接使用 `pipelines/h1.yaml`、`h2.yaml`、`l1.yaml` 是 SDKless 路徑。若要修改 Task，使用[SDK 原始碼](pipelines/pipeline.py)，從固定 commit `8279bb0cd394628c77d28a4c28174334e57ddb4f` 建立 KFP 2.17.0；原始實驗重建 wheel 的原因是先前使用的 wheel 缺少 unified SDK 所需模組，不能只依版本字串判斷。

以下命令供需要重新編譯的控制端執行，需要網路、Python 3.11 以上、tar 與 sha256sum。來源 archive 已包含 `pipeline_spec_pb2.py`、`kubernetes_executor_config_pb2.py` 與 `server_api/__init__.py`；先檢查它們，再建置，不需要先使用未固定版本的 generator image。

```bash
$ export BOOK_REPO="$PWD"
$ export SDK_BUILD_ROOT="$(mktemp -d /tmp/kfp-sdk-8279bb0.XXXXXX)"
$ curl --fail --location 'https://codeload.github.com/kubeflow/pipelines/tar.gz/8279bb0cd394628c77d28a4c28174334e57ddb4f' --output "$SDK_BUILD_ROOT/source.tar.gz"
$ printf '%s  %s\n' '23e1b6b642225fa184d2239244531e017afe9a0589b7de517e83c491393c4895' "$SDK_BUILD_ROOT/source.tar.gz" | sha256sum --check -
$ tar -xzf "$SDK_BUILD_ROOT/source.tar.gz" -C "$SDK_BUILD_ROOT"
$ export SDK_SOURCE="$SDK_BUILD_ROOT/pipelines-8279bb0cd394628c77d28a4c28174334e57ddb4f"
$ test -f "$SDK_SOURCE/sdk/python/kfp/pipeline_spec/pipeline_spec_pb2.py"
$ test -f "$SDK_SOURCE/sdk/python/kfp/kubernetes/kubernetes_executor_config_pb2.py"
$ test -f "$SDK_SOURCE/sdk/python/kfp/server_api/__init__.py"
$ python3 -m venv "$SDK_BUILD_ROOT/venv"
$ export SDK_PYTHON="$SDK_BUILD_ROOT/venv/bin/python"
$ "$SDK_PYTHON" -m pip install 'build==1.6.1' 'hatchling==1.28.0'
$ "$SDK_PYTHON" -m build --wheel --no-isolation --outdir "$SDK_BUILD_ROOT/dist" "$SDK_SOURCE/sdk/python"
$ "$SDK_PYTHON" -m pip install "$SDK_BUILD_ROOT"/dist/kfp-2.17.0-*.whl
$ "$SDK_PYTHON" -c 'from importlib.metadata import version; from kfp import kubernetes; assert version("kfp") == "2.17.0"; assert hasattr(kubernetes, "add_resource_claim"); print("KFP ResourceClaim API available")'
$ "$SDK_PYTHON" -m pip check
$ "$SDK_PYTHON" "$BOOK_REPO/labs/06-heterogeneous/kubeflow/pipelines/pipeline.py" --case h1 --output "$SDK_BUILD_ROOT/h1.yaml"
$ "$SDK_PYTHON" "$BOOK_REPO/labs/06-heterogeneous/kubeflow/pipelines/pipeline.py" --case h2 --output "$SDK_BUILD_ROOT/h2.yaml"
$ "$SDK_PYTHON" "$BOOK_REPO/labs/06-heterogeneous/kubeflow/pipelines/pipeline.py" --case l1 --output "$SDK_BUILD_ROOT/l1.yaml"
```

編譯結果以第二份 YAML document 的 `platforms.kubernetes.deploymentSpec.executors.exec-gpu-task.podResourceClaims` 保存 ResourceClaimTemplate 名稱。Namespace 在提交時決定，不需要在 pipeline 中指定 worker node。固定 commit 的[SDK 實作](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/kfp/kubernetes/pod_resource_claim.py)與[wheel 建置檢查](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/hatch_build.py)可用來核對 API 與模組；image 及本地輸入的來源整理在[SOURCES.md](install/SOURCES.md)。

成功建立新的 venv 只證明 SDK 的重建與編譯路徑，並不等於在全新的叢集完成部署。縮短版本的結果集中於[實機驗證紀錄](../VERIFIED.md)；其中記錄了原生與容量選卡，以及修正後的 KFP 安裝、H1／H2／L1 與解除安裝。
