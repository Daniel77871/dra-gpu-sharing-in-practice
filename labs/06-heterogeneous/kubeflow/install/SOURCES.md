# KFP 安裝與 pipeline 來源

安裝使用 E4B H1 第一輪的 standalone 後端輸入作為基礎：`heterogeneous-capacity/h1-r1/setup/backend/backend-input-redacted.yaml`、`official-rendered-redacted.yaml` 與 `backend-manifest.json`。對應 SDK、工作負載、claim 與容量結果的完整來源索引見[實驗資料來源](../../../../data/e4b/SOURCES.md)。

`objects.json` 是可由 kustomize 讀取的 Kubernetes List。它以原始 66 個物件的後端設定與驗證 schema 為基礎，改用新的 Namespace、角色標籤與 tag@digest，移除 CRD schema 的說明文字及 Secret 的憑證值，並增加一個本次安裝專用的 DeviceClass。初始化 signing-key Job 使用 `restartPolicy: Never` 與 `backoffLimit: 0`，讓初始化失敗留在首次嘗試，便於檢查。安裝時以新 Namespace 產生 MySQL 與物件儲存的憑證，MySQL 初始化也改為讀取 API 使用的同一個 Secret；JSON 中的 Unicode escape 只用來表達 CRD 欄位名稱和命令參數，解析後的 API 欄位不變。

| 原始輸入 | 內容 | 本套件 |
|---|---|---|
| KFP commit `8279bb0cd394628c77d28a4c28174334e57ddb4f` 的 platform-agnostic standalone | namespaced RBAC、API、UI、MySQL、SeaweedFS、KFP controllers | `objects.json` |
| 同一來源的 Argo namespace install v4.1.2 | namespaced workflow-controller 與 8 個 Argo CRD | `objects.json` |
| 同一來源的 scheduledworkflows／viewers CRD | 2 個 Kubeflow CRD | `objects.json` |
| 原始 `backend.py` | dra-non、replicas 1、local-path PVC 5Gi、cache 關閉、namespace-local artifact provider | `objects.json` |
| 原始 `config/images.json` 與 E4B `versions.json` | 不可變 image digests | `images.json` |
| E4B `pipeline.py` 與 H1／H2／L1 的 `pipeline-compiled.yaml` | 真正的 KFP IR 與 PodResourceClaims | `../pipelines/pipeline.py` 與三份 compiled YAML |
| E4 `gpu_workload.py` 與 E4B `gpu_workload.py` | CUDA 核心與成功／受控失敗路徑 | `../workload/gpu_primitives.py`、`gpu_workload.py` |

## 版本與映像檔

KFP 安裝 manifest 的 appVersion 與後端映像檔 tag 是 2.17.1，SDK 版本則是 2.17.0；兩者同來自上述固定 commit。digest 取自原始 `experiments/kubeflow_dra/config/images.json`，KFP 與第三方 tag 取自 H1 第一輪的 `official-rendered-redacted.yaml`，不是由 SDK 版本推算。

| 名稱 | tag | digest |
|---|---|---|
| kfp-api-server | 2.17.1 | `sha256:fb0822260617401eb3f35bb9856030046bdd2cd26e4a4360f14c12c23d0e418d` |
| kfp-driver | 2.17.1 | `sha256:b2dd28deed025f85c5c67c3bf21ebbf64e90761e57331ac30b4057c52326264a` |
| kfp-launcher | 2.17.1 | `sha256:d0847ea2dda9c6120d33e3dd6f5539b679c84b95aa544fa39876c0cf09c60f09` |
| kfp-frontend | 2.17.1 | `sha256:130dbbbaaa0ac7c1e200f38eda7ac66d708891014eb8cb8b9b78dfe14260c107` |
| kfp-persistence-agent | 2.17.1 | `sha256:3798472962a78c621900b38a268805b3494f8607e591ab32f151adf4739941c1` |
| kfp-scheduled-workflow-controller | 2.17.1 | `sha256:2e725bce548953c7590832e56581ca51435b50f4b6fd253a7f700c9ef1003063` |
| kfp-viewer-crd-controller | 2.17.1 | `sha256:6a381f9503391f5ea09f30e6e1f8458fd04ab37bb23d741c3eccd6fd2be526ed` |
| mysql | 8.4 | `sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242` |
| seaweedfs | 4.48 | `sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d` |
| alpine | 3.23 | `sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0` |
| workflow-controller | v4.1.2 | `sha256:466192f538abfe750157c5bb5c0b883137b19f67af27c5636ab15bc853c96668` |
| argoexec | v4.1.2 | `sha256:4a7b9a9859b63e7aea7927c69fc336a5ef07f8dbea91ae2e175cda8788d972e9` |
| pytorch | 2.5.1-cuda12.4-cudnn9-runtime | `sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755` |

PyTorch 的 tag／digest 對應沿用原始 `IMPLEMENTATION_WALKTHROUGH.md` 的映像檔紀錄。`images.json` 也列出前置 provider 的 pins：HAMi-DRA v0.1.0 與 NVIDIA DRA v0.5.0，分別來自原始 `EXPERIMENT_2_HAMI.md` 與 E4B `versions.json`；KFP 安裝本身不部署這兩個 provider。

## SDK archive 與建置

固定 commit 的來源 archive SHA256 是 `23e1b6b642225fa184d2239244531e017afe9a0589b7de517e83c491393c4895`，Argo v4.1.2 archive SHA256 是 `83f981ac5524d80bc21c9cbf9be929f02101525110c9ec3c51e2d5caed67c682`。KFP archive 已包含 pipeline spec、Kubernetes platform 與 server API 的 Python bindings；它們須被 wheel 保留。原始 E4B `sdk_rebuild.py` 使用 build 1.6.1、hatchling 1.28.0 建立 KFP 2.17.0，再檢查 ResourceClaim API 可載入與依賴一致性。

固定 commit 的對照位置是[standalone kustomization](https://github.com/kubeflow/pipelines/tree/8279bb0cd394628c77d28a4c28174334e57ddb4f/manifests/kustomize/env/platform-agnostic)、[ResourceClaim API](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/kfp/kubernetes/pod_resource_claim.py)及[wheel bindings 檢查](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/hatch_build.py)。REST endpoints 與 request fields 對照同一 commit 的 `backend/api/v2beta1/{experiment,run,healthz}.proto` 及 `backend/src/apiserver/server/pipeline_upload_server.go`。

## 資料庫初始化與診斷

E4 與 E4B 的 `backend-input-redacted.yaml` 保留相同的 MySQL authentication args 與空密碼初始化開關；API 的 `DBCONFIG_PASSWORD`、`DBCONFIG_MYSQLCONFIG_PASSWORD` 都引用 `mysql-secret/password`。對照固定來源的 `manifests/kustomize/base/installs/generic/mysql-secret.yaml`，原始 Secret 使用 root 使用者與空密碼。來源 archive 與後端輸入的位置見 [E4](../../../../data/e4/SOURCES.md)及 [E4B](../../../../data/e4b/SOURCES.md)；對照區段為 Secret 18328–18329 行、API refs 18531 與 18590 行、MySQL 19273–19278 行。

套件的安裝器會產生非空憑證，因此 [MySQL Deployment](objects.json) 用 `MYSQL_ROOT_PASSWORD` 引用同一個 Secret，取代空密碼初始化開關。[MySQL 固定 entrypoint](https://raw.githubusercontent.com/docker-library/mysql/01f90d87012e46cd174073bba02d64e9fc693ed3/8.4/docker-entrypoint.sh) 的 `docker_setup_env` 讀取此環境變數，`docker_setup_db` 用它建立 root 帳號；若資料目錄已有資料庫，初始化步驟會略過。這也是修正後需使用新 Namespace 與 PVC 的原因。

固定 KFP archive 的 `backend/src/apiserver/client_manager/client_manager.go` 在 638–669 行先 `sql.Open`，再重試 `CREATE DATABASE`，API 自行建立資料庫；`backend/src/apiserver/common/config.go` 的 146–150 行把未設定 `ExtraParams` 記為 Info 並回傳 nil。這些來源用來區分初始化等待與設定錯誤，修正保留原始映像檔、authentication args、DB 名稱及 startup probe，沒有加入額外 DB 初始化 Job。固定版本的公開對照位置是 [client_manager.go](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/backend/src/apiserver/client_manager/client_manager.go)與 [common/config.go](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/backend/src/apiserver/common/config.go)；上述程式核對使用資料來源索引指向的本地固定 archive。

失敗診斷與部分安裝的清除流程由 [kfp_state.py](../scripts/kfp_state.py)實作。診斷只在 Namespace UID 與歸屬核對相符後讀取狀態與 logs，不查詢 Secret 值；卸除則以已記錄的 Namespace 為內容範圍，逐一清理自有叢集物件並彙整錯誤。密碼來源一致性、執行期物件、安裝失敗、逾時、UID 替換與 PV 紀錄保留情境的回歸驗證見 [test_lab06_kfp.py](../../../../tests/test_lab06_kfp.py)，原有輸入相容性與 UID 檢查保留於 [test_lab06.py](../../../../tests/test_lab06.py)。
