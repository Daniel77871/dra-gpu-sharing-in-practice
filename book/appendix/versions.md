# 附錄 B　版本與映像檔

## 本頁要回答的問題

書中結果使用哪些元件版本與映像檔內容？本表區分原始叢集、容器工作負載、KFP 整合與離線檢查工具，讓讀者可以核對相同條件再執行復現。

## 背景原理

Tag 表示版本名稱，digest 固定映像檔內容；兩者一起保留才方便辨識與重現。原始安裝以 Helm chart 和版本設定為主，復現的 GPU 工作與替代 provider 則固定觀察到的 digest；沒有在套件中固定的 digest 不從 tag 推測。[部署環境](../02-deploy/README.md#環境版本)、[HAMi-DRA 函式庫身分](../04-isolation/hami-dra.md#函式庫身分與原始碼的界線)

## 部署與設定

### 叢集與控制端

| 元件 | 使用版本 | 用途與來源 |
| --- | --- | --- |
| Ubuntu | 24.04.3 LTS | 所有節點；[環境版本](../02-deploy/README.md#環境版本) |
| Linux kernel | GPU 節點 `6.8.0-142-generic`；其餘節點 `6.8.0-90-generic` | [原叢集環境表](../02-deploy/README.md#環境版本)；安裝時需[核對可取得的 headers](../02-deploy/node-prep.md#核對-headers-是否仍可下載) |
| Kubernetes server、kubelet、kubectl | v1.37.0 | [環境表](../02-deploy/README.md#環境版本)、[client／server 版本紀錄](../../data/e1/version.json) |
| CRI-O | v1.37.1 | 安裝指南寫 v1.37 系列，實測為此版本；[環境表](../02-deploy/README.md#環境版本) |
| Cilium | 1.20.2 | 原叢集 CNI；[環境表](../02-deploy/README.md#環境版本) |
| GPU Operator chart、Operator、validator | v26.7.0 | [安裝命令](../02-deploy/gpu-operator.md#使用原本的-helm-參數)、[values](../../labs/02-setup/values/gpu-operator.yaml)；套件未另固定 Operator digest |
| NVIDIA driver | 595.91.07；driver image tag `595.91.07-ubuntu24.04` | [driver Pod 驗證](../02-deploy/gpu-operator.md#在-driver-pod-內確認驅動)；套件未另固定 driver image digest |
| 驅動可支援的 CUDA 版本 | `nvidia-smi` 顯示 13.2 | [driver 驗證輸出](../02-deploy/gpu-operator.md#在-driver-pod-內確認驅動)；與下表容器 CUDA runtime 的版本分開閱讀 |
| NVIDIA DRA driver 與 chart | v0.5.0 | [DRA 部署](../02-deploy/dra-driver.md)、[values](../../labs/02-setup/values/dra-driver.yaml) |
| HAMi-DRA k8s-dra-driver | v0.1.0 | [HAMi-DRA provider](../04-isolation/hami-dra.md#原始環境與名稱)、[provider manifest](../../labs/04-isolation/hami-dra/manifests/provider/daemonset.yaml)；套件直接套用 YAML，不以 chart 版本代替 driver image 版本 |
| Helm | v4.3.0 | 原叢集控制端；[部署實機驗證紀錄](../../labs/02-setup/VERIFIED.md#環境) |
| 控制端 Python | 3.12.3 | 原叢集部署驗證主機；[實機驗證紀錄](../../labs/02-setup/VERIFIED.md#環境) |
| kubectl 內建 kustomize | v5.8.1 | 原始 client 的[版本紀錄](../../data/e1/version.json)；與離線渲染工具分開固定 |

### GPU 工作與 provider 映像檔

| 元件 | 固定映像檔 | 使用位置 |
| --- | --- | --- |
| PyTorch 2.5.1+cu124；CUDA runtime 12.4；tag 標示 cuDNN 9 | `docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755` | [部署工作](../../labs/02-setup/manifests/04-gpu-smoke-a40.yaml)、[OOM](../03-oom/README.md#環境與命名)、[隔離](../04-isolation/README.md)、[效能](../05-performance/README.md#共用輸入與物件名稱)、[異質工作](../06-heterogeneous/selection.md#共用-namespace-與工作負載) |
| NVIDIA DRA driver v0.5.0 | `nvcr.io/nvidia/dra-driver-nvidia-gpu:v0.5.0@sha256:2f76a02532bea530418693bcf382f2094cb40dc8466cefdbb9af923a232e738b` | [ConsumableShares provider](../../labs/04-isolation/consumable-shares/manifests/provider/daemonset.yaml)、[原生異質選卡](../../labs/06-heterogeneous/selection/native/kustomization.yaml) |
| HAMi-DRA v0.1.0 | `docker.io/projecthami/k8s-dra-driver:v0.1.0@sha256:ae7e844acbd099a109bfa84296905ea941eed292a7991c06e2eeee443457ede3` | [HAMi-DRA provider](../../labs/04-isolation/hami-dra/manifests/provider/daemonset.yaml)、[效能相依套件](../../labs/05-performance/README.md#前置條件)、[異質容量 provider](../../labs/06-heterogeneous/scripts/install-provider.sh) |
| 網路診斷 | `busybox:1.36`；原指南未固定 digest | [DNS 與 egress 排查](../02-deploy/troubleshooting.md#dns-與-egress)，不作為 GPU 工作負載 |

工作負載中的 `pytorch/pytorch` 與 `docker.io/pytorch/pytorch` 指向同一個 Docker Hub 名稱，本表統一寫出 registry。cuDNN 只記錄 image tag 指出的主版本，不推測未另量測的細部版本。

HAMi-core `libvgpu.so` 的容器與 host 檔案 SHA256 均為 `56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f`。[函式庫證據與界線](../04-isolation/hami-dra.md#函式庫身分與原始碼的界線)說明目前不能把這個二進位檔精確對應到 HAMi-core commit；書中固定 commit 的原始碼連結用於機制核對，並非此檔案的版本證明。

### KFP 與配套映像檔

下表直接整理[固定映像檔清單](../../labs/06-heterogeneous/kubeflow/install/images.json)，使用位置是 [KFP standalone 後端](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端)。GPU executor 使用上一表的 PyTorch 映像檔，不另更換 CUDA 環境。

| 元件 | 固定映像檔 | 使用位置 |
| --- | --- | --- |
| KFP API server | `ghcr.io/kubeflow/kfp-api-server:2.17.1@sha256:fb0822260617401eb3f35bb9856030046bdd2cd26e4a4360f14c12c23d0e418d` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP driver | `ghcr.io/kubeflow/kfp-driver:2.17.1@sha256:b2dd28deed025f85c5c67c3bf21ebbf64e90761e57331ac30b4057c52326264a` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP launcher | `ghcr.io/kubeflow/kfp-launcher:2.17.1@sha256:d0847ea2dda9c6120d33e3dd6f5539b679c84b95aa544fa39876c0cf09c60f09` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP frontend | `ghcr.io/kubeflow/kfp-frontend:2.17.1@sha256:130dbbbaaa0ac7c1e200f38eda7ac66d708891014eb8cb8b9b78dfe14260c107` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP persistence agent | `ghcr.io/kubeflow/kfp-persistence-agent:2.17.1@sha256:3798472962a78c621900b38a268805b3494f8607e591ab32f151adf4739941c1` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP scheduled workflow controller | `ghcr.io/kubeflow/kfp-scheduled-workflow-controller:2.17.1@sha256:2e725bce548953c7590832e56581ca51435b50f4b6fd253a7f700c9ef1003063` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| KFP viewer CRD controller | `ghcr.io/kubeflow/kfp-viewer-crd-controller:2.17.1@sha256:6a381f9503391f5ea09f30e6e1f8458fd04ab37bb23d741c3eccd6fd2be526ed` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| MySQL | `docker.io/library/mysql:8.4@sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| SeaweedFS | `ghcr.io/chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| Alpine | `docker.io/library/alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| Argo workflow controller | `quay.io/argoproj/workflow-controller:v4.1.2@sha256:466192f538abfe750157c5bb5c0b883137b19f67af27c5636ab15bc853c96668` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |
| Argo executor | `quay.io/argoproj/argoexec:v4.1.2@sha256:4a7b9a9859b63e7aea7927c69fc336a5ef07f8dbea91ae2e175cda8788d972e9` | [安裝輸入](../../labs/06-heterogeneous/kubeflow/install/images.json)、[章節版本表](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |

### KFP 來源與 SDK

| 元件或來源 | 固定版本與內容 | 使用位置 |
| --- | --- | --- |
| KFP 後端與 SDK 原始碼 | commit `8279bb0cd394628c77d28a4c28174334e57ddb4f` | [後端來源](../../labs/06-heterogeneous/kubeflow/install/SOURCES.md)、[SDK 重建](../06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| KFP source archive | SHA256 `23e1b6b642225fa184d2239244531e017afe9a0589b7de517e83c491393c4895` | [來源 archive 檢查](../06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| KFP SDK wheel | 2.17.0，由上列 commit 重建；與原發佈 wheel 內容不同 | [SDK 重建與 API 驗證](../06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| SDK build | `build==1.6.1`、`hatchling==1.28.0`；Python 3.11 以上 | [建置命令](../06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| Argo source archive | v4.1.2；SHA256 `83f981ac5524d80bc21c9cbf9be929f02101525110c9ec3c51e2d5caed67c682` | [來源清單](../../labs/06-heterogeneous/kubeflow/install/images.json)、[後端安裝](../06-heterogeneous/kubeflow.md#固定來源的-standalone-後端) |

KFP 後端 image tag 為 2.17.1，重建 SDK wheel 標示 2.17.0；它們是不同 artifact，不能只靠其中一個版本字串判斷另一個是否相容。實驗的整合與生命週期結果見 [KFP 驗證](../06-heterogeneous/kubeflow.md#驗證)。

### 離線檢查與圖表工具

| 元件 | 固定版本 | 使用位置 |
| --- | --- | --- |
| Python | 3.12 系列；本機檢查為 3.12.13，CI 選 3.12 | [根目錄檢查說明](../../README.md#使用需求)、[CI](../../.github/workflows/check.yml) |
| kustomize | v5.4.3 | `.work/bin/kustomize`；[CI 安裝](../../.github/workflows/check.yml)、[部署套件渲染測試](../../tests/test_lab02.py) |
| matplotlib | 3.11.2 | [requirements.txt](../../requirements.txt) |
| numpy | 2.5.4 | [requirements.txt](../../requirements.txt) |
| PyYAML | 6.0.3 | [requirements.txt](../../requirements.txt) |
| pytest | 9.1.1 | [requirements.txt](../../requirements.txt) |

## 驗證

版本表來自章節環境表、套件 manifests／values、固定 KFP 安裝輸入與實機驗證紀錄。離線檢查可確認 manifest 渲染與分析結果，但不會證明其他版本叢集的行為；原叢集縮短版的日期與結果見[復現對照表](reproduction-map.md)。

## 重點整理

重現時同時核對版本、digest、功能開關與 Claim 拓撲。原始 client 的工具版本、離線測試工具與 SDK 建置環境各有用途，請勿把它們合併成單一叢集版本要求。

<!-- nav:start -->
[← 上一頁：附錄 A　名詞對照表](glossary.md) ｜ [目錄](../README.md) ｜ [下一頁：附錄 C　復現對照表 →](reproduction-map.md)
<!-- nav:end -->
