# 附錄 D　參考資料

## 本頁要回答的問題

各章的機制、API 與硬體說明引用了哪些外部資料？本頁按主題整理全書外部 URL，保留原引用的版本與片段，並提供回到書中使用位置的連結。

## 背景原理

固定 tag 或 commit 的原始碼連結用於核對指定版本；未固定版本的官方說明可能隨時間改變。參考頁中的版本不等於原叢集部署版本，實際使用的元件與 digest 請查[版本與映像檔](versions.md)。

## 結果

清單由 [extract_references.py](../../tools/extract_references.py) 擷取書中的 Markdown 連結及程式範例 URL，合併相關延伸來源後依完整 URL 去重。來源表涵蓋 CUDA／MPS、HAMi-core 機制與歷史案例，但不把參考原始碼版本當成實測二進位檔的身分證明。

```sh
$ python3 tools/extract_references.py
$ python3 tools/extract_references.py --check
```

### Kubernetes DRA 與工作生命週期

| 參考資料 | 書中使用位置 |
| --- | --- |
| [enhancements master — KEP 4816](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/4816-dra-prioritized-list/README.md) | [06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#優先替代清單) |
| [enhancements master — KEP 5075](https://raw.githubusercontent.com/kubernetes/enhancements/master/keps/sig-scheduling/5075-dra-consumable-capacity/README.md) | [04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#kubernetes-的容量消耗是排程帳本) |
| [enhancements master — keps/sig-node/4381-dra-structured-parameters](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/4381-dra-structured-parameters) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#從需求到容器) |
| [enhancements master — Kubernetes KEP-4815 — DRA Partitionable Devices](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/4815-dra-partitionable-devices/README.md) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| [enhancements master — Kubernetes KEP-5075 — DRA Consumable Capacity](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5075-dra-consumable-capacity/README.md) | [01-why-dra/README.md](../../book/01-why-dra/README.md#背景原理)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#需求可以如何延伸)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#可消耗-memory-與-cores) |
| [Kubernetes v1.37 ResourceSlice API](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-slice-v1/) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice) |
| [kubernetes v1.37.0 — Resource API 型別](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/staging/src/k8s.io/api/resource/v1/types.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#實際的裝置分類)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#實際的裝置清單)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#需求可以如何延伸)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#驅動專屬設定)、[02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#優先替代清單) |
| [kubernetes v1.37.0 — 物件 metadata 型別](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/staging/src/k8s.io/apimachinery/pkg/apis/meta/v1/types.go) | [02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期) |
| [kubernetes v1.37.0 — API 型別（L819-L835）](https://github.com/kubernetes/kubernetes/blob/v1.37.0/staging/src/k8s.io/api/resource/v1/types.go#L819-L835) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice) |
| [kubernetes v1.37.0 — DynamicResources 排程外掛](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/pkg/scheduler/framework/plugins/dynamicresources/dynamicresources.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#從需求到容器)、[06-heterogeneous/README.md](../../book/06-heterogeneous/README.md#背景原理)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#多張卡符合時究竟選誰)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#pending-後為何同一工作能繼續) |
| [kubernetes v1.37.0 — kubelet Device Manager](https://github.com/kubernetes/kubernetes/blob/v1.37.0/pkg/kubelet/cm/devicemanager/manager.go) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#背景原理) |
| [kubernetes v1.37.0 — kubelet DRA Manager](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/pkg/kubelet/cm/dra/manager.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#從需求到容器)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#解除配置與刪除的時點) |
| [kubernetes v1.37.0 — Kubernetes 功能開關](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/pkg/features/kube_features.go) | [04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#kubernetes-的容量消耗是排程帳本) |
| [kubernetes v1.37.0 — pkg/kubelet/kubelet.go](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/pkg/kubelet/kubelet.go) | [02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期) |
| [kubernetes v1.37.0 — ResourceClaim controller](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/pkg/controller/resourceclaim/controller.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#解除配置與刪除的時點)、[02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期)、[06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#成功失敗取消與重試的釋放) |
| [kubernetes v1.37.0 — 功能開關成熟度與 metrics](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.37.0/staging/src/k8s.io/component-base/featuregate/feature_gate.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| [Kubernetes — Allocate Devices with DRA](https://kubernetes.io/docs/tasks/configure-pod-container/assign-resources/allocate-devices-dra/) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#物件如何連接)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#實際的裝置申請) |
| [Kubernetes — Background 回收](https://kubernetes.io/docs/concepts/architecture/garbage-collection/) | [02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期) |
| [Kubernetes — Job backoffLimit 的失敗處理](https://kubernetes.io/docs/concepts/workloads/controllers/job/#pod-backoff-failure-policy) | [03-oom/README.md](../../book/03-oom/README.md#b-的批次壓力角色) |
| [Kubernetes — DaemonSet 更新策略說明](https://kubernetes.io/docs/tasks/manage-daemon/update-daemon-set/#daemonset-update-strategy) | [04-isolation/README.md](../../book/04-isolation/README.md#只讓一個-provider-公布目標-gpu) |
| [Kubernetes — Device Plugin implementation](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/#device-plugin-implementation) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#背景原理) |
| [Kubernetes — Device Plugin registration and resource requests](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/#device-plugin-registration) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#整數資源代表什麼) |
| [Kubernetes — Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#背景原理)、[01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#device-plugin-表達不了的需求) |
| [Kubernetes — Device Taints and Tolerations](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| [Kubernetes — DRA API Objects](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/dra-api/) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#本頁要回答的問題)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#物件如何連接)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度)、[02-deploy/verify.md](../../book/02-deploy/verify.md#背景原理)、[02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#屬性條件與任何-gpu)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#優先替代清單)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#多張卡符合時究竟選誰) |
| [Kubernetes — DRA API Objects, Prioritized list](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/dra-api/#prioritized-list) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#需求可以如何延伸) |
| [Kubernetes — DRA Features](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/dra-features/) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| [Kubernetes — DRA Features, Consumable capacity](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/dra-features/#consumable-capacity) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#物件如何連接)、[01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#需求可以如何延伸)、[04-isolation/README.md](../../book/04-isolation/README.md#三層隔離模型)、[04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#kubernetes-的容量消耗是排程帳本)、[06-heterogeneous/README.md](../../book/06-heterogeneous/README.md#背景原理)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#背景原理) |
| [Kubernetes — Dynamic Resource Allocation](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/) | [01-why-dra/README.md](../../book/01-why-dra/README.md#本章要回答的問題)、[01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#device-plugin-表達不了的需求) |
| [Kubernetes — Enable Or Disable Feature Gates, Check via metrics endpoint](https://kubernetes.io/docs/tasks/administer-cluster/configure-feature-gates/#check-via-metrics-endpoint) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| [Kubernetes — Feature Gates](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#kubernetes-功能成熟度)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#驅動的功能開關與互斥條件)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#可消耗-memory-與-cores)、[06-heterogeneous/selection.md](../../book/06-heterogeneous/selection.md#優先替代清單) |
| [Kubernetes — How DRA Works](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/how-dra-works/) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#本頁要回答的問題)、[02-deploy/verify.md](../../book/02-deploy/verify.md#背景原理)、[06-heterogeneous/README.md](../../book/06-heterogeneous/README.md#背景原理) |
| [Kubernetes — Job 完成後的管理](https://kubernetes.io/docs/concepts/workloads/controllers/job/) | [02-deploy/verify.md](../../book/02-deploy/verify.md#rtx-4090-job)、[02-deploy/verify.md](../../book/02-deploy/verify.md#觀察-claim-生命週期) |
| [Kubernetes — kubectl wait 說明](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_wait/) | [02-deploy/verify.md](../../book/02-deploy/verify.md#手動放行) |
| [Kubernetes — 安全 drain 節點](https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/) | [02-deploy/node-prep.md](../../book/02-deploy/node-prep.md#drain-與重新開機) |
| [Kubernetes — 裝置配置流程](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/how-dra-works/#workflow-for-kubernetes) | [03-oom/README.md](../../book/03-oom/README.md#一個-claim-如何讓兩個-pod-共用裝置) |
| [Kubernetes 記憶體限制範例](https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/#exceed-a-containers-memory-limit) | [03-oom/README.md](../../book/03-oom/README.md#cuda-oom-與容器-oomkilled) |

### NVIDIA DRA driver

| 參考資料 | 書中使用位置 |
| --- | --- |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/controller.yaml（L15-L21）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/controller.yaml#L15-L21) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#驗證) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-gpu.yaml](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-gpu.yaml) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#實際的裝置分類) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-gpu.yaml（L1-L15）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-gpu.yaml#L1-L15) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#三種-deviceclass) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-mig.yaml](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-mig.yaml) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#三種-deviceclass) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-vfio-gpu.yaml](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/deviceclass-vfio-gpu.yaml) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#三種-deviceclass) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/kubeletplugin.yaml（L15-L23）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/kubeletplugin.yaml#L15-L23) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#驗證) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/rbac-kubeletplugin.yaml](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/rbac-kubeletplugin.yaml) | [04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#重用原始-serviceaccount-與-rbac) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/validation.yaml](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/validation.yaml) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#使用原本的-helm-參數) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/templates/validation.yaml（L33-L43）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/templates/validation.yaml#L33-L43) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#安裝命令與參數) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/values.yaml](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/values.yaml) | [02-deploy/troubleshooting.md](../../book/02-deploy/troubleshooting.md#dra-先於-driver-root-啟動) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/values.yaml（L109-L121）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/values.yaml#L109-L121) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#功能開關與相依關係) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/values.yaml（L19-L23）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/values.yaml#L19-L23) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#背景原理) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/values.yaml（L319-L352）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/values.yaml#L319-L352) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#限制) |
| [dra-driver-nvidia-gpu v0.5.0 — deployments/helm/dra-driver-nvidia-gpu/values.yaml（L79-L121）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/deployments/helm/dra-driver-nvidia-gpu/values.yaml#L79-L121) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#安裝命令與參數) |
| [dra-driver-nvidia-gpu v0.5.0 — DRA driver（L488-L505）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/driver.go#L488-L505) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#背景原理) |
| [dra-driver-nvidia-gpu v0.5.0 — driver 功能開關](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/pkg/featuregates/featuregates.go) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#驅動的功能開關與互斥條件) |
| [dra-driver-nvidia-gpu v0.5.0 — driver 功能開關](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/pkg/featuregates/featuregates.go) | [04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#nvidia-v050-如何公布-memory)、[04-isolation/mps.md](../../book/04-isolation/mps.md#nvidia-dra-如何將配額接到-claim) |
| [dra-driver-nvidia-gpu v0.5.0 — driver 功能開關（L250-L278）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/pkg/featuregates/featuregates.go#L250-L278) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#功能開關與相依關係) |
| [dra-driver-nvidia-gpu v0.5.0 — driver 功能開關（L47-L207）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/pkg/featuregates/featuregates.go#L47-L207) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#功能開關與相依關係) |
| [dra-driver-nvidia-gpu v0.5.0 — GPU 分區狀態（L29-L42）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/partitions.go#L29-L42) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice) |
| [dra-driver-nvidia-gpu v0.5.0 — GPU 屬性盤點（L168-L210）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/deviceinfo.go#L168-L210) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice) |
| [dra-driver-nvidia-gpu v0.5.0 — GpuConfig 預設值](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/api/nvidia.com/resource/v1beta1/gpuconfig.go) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice)、[03-oom/README.md](../../book/03-oom/README.md#一個-claim-如何讓兩個-pod-共用裝置)、[05-performance/README.md](../../book/05-performance/README.md#時間切片讓不同-context-輪流執行) |
| [dra-driver-nvidia-gpu v0.5.0 — hack/kubelet-plugin-prestart.sh](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/hack/kubelet-plugin-prestart.sh) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#背景原理)、[02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#安裝命令與參數) |
| [dra-driver-nvidia-gpu v0.5.0 — kubelet plugin 啟動](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/cmd/gpu-kubelet-plugin/main.go) | [04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#nvidia-v050-如何公布-memory) |
| [dra-driver-nvidia-gpu v0.5.0 — NVML 與 MPS 控制](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/nvlib.go) | [05-performance/README.md](../../book/05-performance/README.md#時間切片讓不同-context-輪流執行) |
| [dra-driver-nvidia-gpu v0.5.0 — NVML 與 MPS 控制（L492-L565）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/nvlib.go#L492-L565) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#一份-a40-resourceslice) |
| [dra-driver-nvidia-gpu v0.5.0 — templates/mps-control-daemon.tmpl.yaml](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/templates/mps-control-daemon.tmpl.yaml) | [04-isolation/mps.md](../../book/04-isolation/mps.md#prepare-建立的是哪一個服務) |
| [dra-driver-nvidia-gpu v0.5.0 — v0.5.0 DRA 安裝說明](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/site/content/docs/install.md) | [02-deploy/README.md](../../book/02-deploy/README.md#背景原理) |
| [dra-driver-nvidia-gpu v0.5.0 — v0.5.0 官方 OCI 安裝方式（L17-L35）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/site/content/docs/install.md#L17-L35) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#確認-chart-來源) |
| [dra-driver-nvidia-gpu v0.5.0 — 分享設定 API](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/api/nvidia.com/resource/v1beta1/sharing.go) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#驅動專屬設定)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#驅動的功能開關與互斥條件)、[05-performance/README.md](../../book/05-performance/README.md#時間切片讓不同-context-輪流執行) |
| [dra-driver-nvidia-gpu v0.5.0 — 分享設定 API](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/cmd/gpu-kubelet-plugin/sharing.go) | [04-isolation/mps.md](../../book/04-isolation/mps.md#prepare-建立的是哪一個服務)、[04-isolation/mps.md](../../book/04-isolation/mps.md#cdi-接線與-compute-mode)、[05-performance/README.md](../../book/05-performance/README.md#時間切片讓不同-context-輪流執行) |
| [dra-driver-nvidia-gpu v0.5.0 — 分享設定 API（L191-L239）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/api/nvidia.com/resource/v1beta1/sharing.go#L191-L239) | [04-isolation/mps.md](../../book/04-isolation/mps.md#nvidia-dra-如何將配額接到-claim) |
| [dra-driver-nvidia-gpu v0.5.0 — 分享設定 API（L80-L92）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/api/nvidia.com/resource/v1beta1/sharing.go#L80-L92) | [04-isolation/mps.md](../../book/04-isolation/mps.md#nvidia-dra-如何將配額接到-claim) |
| [dra-driver-nvidia-gpu v0.5.0 — 分享設定 API（L94-L108）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/api/nvidia.com/resource/v1beta1/sharing.go#L94-L108) | [03-oom/README.md](../../book/03-oom/README.md#一個-claim-如何讓兩個-pod-共用裝置) |
| [dra-driver-nvidia-gpu v0.5.0 — 容量消耗記帳](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/cmd/gpu-kubelet-plugin/consumable_shares.go) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#nvidia-v050-如何公布-memory) |
| [dra-driver-nvidia-gpu v0.5.0 — 裝置準備與狀態](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/device_state.go) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#驅動的功能開關與互斥條件) |
| [dra-driver-nvidia-gpu v0.5.0 — 裝置準備與狀態](https://raw.githubusercontent.com/kubernetes-sigs/dra-driver-nvidia-gpu/v0.5.0/cmd/gpu-kubelet-plugin/device_state.go) | [03-oom/README.md](../../book/03-oom/README.md#一個-claim-如何讓兩個-pod-共用裝置)、[04-isolation/consumable-shares.md](../../book/04-isolation/consumable-shares.md#nvidia-v050-如何公布-memory)、[04-isolation/mps.md](../../book/04-isolation/mps.md#prepare-建立的是哪一個服務) |
| [dra-driver-nvidia-gpu v0.5.0 — 裝置準備與狀態（L1330-L1339）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/device_state.go#L1330-L1339) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#功能開關與相依關係) |
| [dra-driver-nvidia-gpu v0.5.0 — 裝置準備與狀態（L1330-L1347）](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/blob/v0.5.0/cmd/gpu-kubelet-plugin/device_state.go#L1330-L1347) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#限制) |

### NVIDIA MPS 與 CUDA

| 參考資料 | 書中使用位置 |
| --- | --- |
| [NVIDIA CUDA 相容性](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#在-driver-pod-內確認驅動) |
| [NVIDIA MPS r595 — When to Use MPS](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#驅動專屬設定)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#背景原理)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[05-performance/README.md](../../book/05-performance/README.md#mps-提供跨行程的並行機會) |
| [NVIDIA R595 的 stream 與工作佇列說明](https://docs.nvidia.com/deploy/mps/595/architecture.html) | [04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#背景原理)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異)、[04-isolation/mps.md](../../book/04-isolation/mps.md#hyper-q-為何能填補空檔)、[04-isolation/mps.md](../../book/04-isolation/mps.md#mps-的三個元件與架構演變)、[04-isolation/mps.md](../../book/04-isolation/mps.md#配置失敗與致命-gpu-故障的差別)、[05-performance/README.md](../../book/05-performance/README.md#時間切片讓不同-context-輪流執行)、[05-performance/README.md](../../book/05-performance/README.md#mps-提供跨行程的並行機會) |
| [NVIDIA R595 的元件定義](https://docs.nvidia.com/deploy/mps/595/index.html) | [04-isolation/mps.md](../../book/04-isolation/mps.md#mps-的三個元件與架構演變) |
| [NVIDIA — 2019 年分享第 12 頁](https://www.nvidia.cn/content/dam/en-zz/zh_cn/assets/webinars/31oct2019c/20191031_MPS_davidwu.pdf#page=12) | [04-isolation/mps.md](../../book/04-isolation/mps.md#hyper-q-為何能填補空檔) |
| [NVIDIA — client 記憶體查詢](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#dynamic-execution-resource-provisioning) | [04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異)、[04-isolation/mps.md](../../book/04-isolation/mps.md#nvml-行程列表要和-mps-身分對照)、[04-isolation/mps.md](../../book/04-isolation/mps.md#解讀) |
| [NVIDIA — CUDA 12.4 的 context 說明](https://docs.nvidia.com/cuda/archive/12.4.0/cuda-c-best-practices-guide/index.html#multiple-contexts) | [04-isolation/mps.md](../../book/04-isolation/mps.md#cuda-context-與時間切片) |
| [NVIDIA — CUDA 12.4 的跨 context 執行規則](https://docs.nvidia.com/cuda/archive/12.4.0/cuda-c-programming-guide/index.html#concurrent-kernel-execution) | [04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#背景原理)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異)、[04-isolation/mps.md](../../book/04-isolation/mps.md#cuda-context-與時間切片)、[04-isolation/mps.md](../../book/04-isolation/mps.md#hyper-q-為何能填補空檔)、[05-performance/README.md](../../book/05-performance/README.md#mps-提供跨行程的並行機會) |
| [NVIDIA — CUDA 12.4.1 的 context 說明](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-driver-api/driver-vs-runtime-api.html) | [03-oom/README.md](../../book/03-oom/README.md#張量以外也會使用-vram) |
| [NVIDIA — CUDA 記憶體管理 API](https://docs.nvidia.com/cuda/cuda-runtime-api/cuda_runtime_api/group__CUDART__MEMORY.html) | [03-oom/README.md](../../book/03-oom/README.md#cuda-oom-與容器-oomkilled) |
| [NVIDIA — MPS device memory limit](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#mps-device-memory-limit) | [04-isolation/README.md](../../book/04-isolation/README.md#三層隔離模型)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異)、[04-isolation/mps.md](../../book/04-isolation/mps.md#配額-oom-時整張-gpu-並未耗盡) |
| [NVIDIA — MPS 的 client 連線條件](https://docs.nvidia.com/deploy/mps/595/architecture.html#client-attach-detach) | [04-isolation/mps.md](../../book/04-isolation/mps.md#解讀) |
| [NVIDIA — nvidia-smi 欄位定義](https://docs.nvidia.com/deploy/nvidia-smi/index.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#在-driver-pod-內確認驅動) |
| [NVIDIA — NVML 管理介面](https://developer.nvidia.com/management-library-nvml) | [01-why-dra/dra-model.md](../../book/01-why-dra/dra-model.md#實際的裝置清單) |
| [NVIDIA — R595 的資源限制介面](https://docs.nvidia.com/deploy/mps/595/appendix-tools-and-interface-reference.html#environment-variables) | [04-isolation/mps.md](../../book/04-isolation/mps.md#mps-的三個元件與架構演變) |
| [NVIDIA — R595 的連線限制](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#client-server-connection-limits) | [04-isolation/mps.md](../../book/04-isolation/mps.md#mps-的三個元件與架構演變) |
| [NVIDIA — 分享第 3 與第 27 頁](https://www.nvidia.cn/content/dam/en-zz/zh_cn/assets/webinars/31oct2019c/20191031_MPS_davidwu.pdf#page=27) | [04-isolation/mps.md](../../book/04-isolation/mps.md#哪些工作可能受惠) |
| [NVIDIA — 分享第 31 至第 35 頁](https://www.nvidia.cn/content/dam/en-zz/zh_cn/assets/webinars/31oct2019c/20191031_MPS_davidwu.pdf#page=31) | [04-isolation/mps.md](../../book/04-isolation/mps.md#哪些工作可能受惠) |
| [NVIDIA 的 client 終止條件](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#client-early-termination) | [04-isolation/mps.md](../../book/04-isolation/mps.md#配置失敗與致命-gpu-故障的差別) |
| [NVIDIA 的 compute mode 建議](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#gpu-compute-modes) | [04-isolation/mps.md](../../book/04-isolation/mps.md#cdi-接線與-compute-mode) |
| [NVIDIA 的監控範圍](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#limitations) | [04-isolation/mps.md](../../book/04-isolation/mps.md#nvml-行程列表要和-mps-身分對照) |
| [NVIDIA 的適用條件](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#identifying-candidate-applications) | [04-isolation/mps.md](../../book/04-isolation/mps.md#哪些工作可能受惠) |
| [NVIDIA 的錯誤侷限範圍](https://docs.nvidia.com/deploy/mps/595/when-to-use-mps.html#memory-protection-and-error-containment) | [04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異)、[04-isolation/mps.md](../../book/04-isolation/mps.md#配置失敗與致命-gpu-故障的差別) |

### HAMi

| 參考資料 | 書中使用位置 |
| --- | --- |
| [HAMi v2.10.0 — classic device plugin 注入](https://github.com/Project-HAMi/HAMi/blob/v2.10.0/pkg/device-plugin/nvidiadevice/nvinternal/plugin/server.go) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#classic-hami-的接入路徑) |
| [HAMi v2.6.0 — 原始碼](https://github.com/Project-HAMi/HAMi/tree/v2.6.0) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#classic-hami-的接入路徑) |
| [HAMi v2.8.0 documentation — GPU Virtualization](https://project-hami.io/docs/v2.8.0/core-concepts/gpu-virtualization) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [HAMi v2.8.0 documentation — HAMi-core Design](https://project-hami.io/docs/v2.8.0/developers/hami-core-design) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#背景原理) |
| [HAMi-core 5496322 — CUDA 記憶體攔截](https://github.com/Project-HAMi/HAMi-core/blob/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/cuda/memory.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#函式庫身分與原始碼的界線)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#sm-比例如何節流) |
| [HAMi-core 5496322 — 使用率回饋與 kernel 提交節流](https://github.com/Project-HAMi/HAMi-core/blob/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/multiprocess/multiprocess_utilization_watcher.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#sm-比例如何節流) |
| [HAMi-core 5496322 — 共享區域與行程槽位](https://raw.githubusercontent.com/Project-HAMi/HAMi-core/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/multiprocess/multiprocess_memory_limit.h) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core 5496322 — 共享記憶體配額](https://github.com/Project-HAMi/HAMi-core/blob/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/multiprocess/multiprocess_memory_limit.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#跨容器與跨-pod-的配額範圍) |
| [HAMi-core 5496322 — 攔截函式庫入口](https://github.com/Project-HAMi/HAMi-core/blob/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/libvgpu.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core 5496322 — 記憶體配置器](https://github.com/Project-HAMi/HAMi-core/blob/5496322f2fb3e71bf1eca014fba3c9bc59ab8ffd/src/allocator/allocator.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core 8c32de6 — 使用率回饋與 kernel 提交節流](https://raw.githubusercontent.com/Project-HAMi/HAMi-core/8c32de630b24f5f7d6355fbeb0034845d3bdafb7/src/multiprocess/multiprocess_utilization_watcher.c) | [05-performance/README.md](../../book/05-performance/README.md#hami-dra-在普通-context-路徑前限制提交速度) |
| [HAMi-core b216ba1 — CUDA context 計量](https://raw.githubusercontent.com/Project-HAMi/HAMi-core/b216ba1be1b8e21488d1c7370ed3357b3049aad1/src/cuda/context.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#函式庫身分與原始碼的界線)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core b216ba1 — NVML 攔截](https://github.com/Project-HAMi/HAMi-core/blob/b216ba1be1b8e21488d1c7370ed3357b3049aad1/src/nvml/hook.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#函式庫身分與原始碼的界線)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core b216ba1 — 工具函式](https://raw.githubusercontent.com/Project-HAMi/HAMi-core/b216ba1be1b8e21488d1c7370ed3357b3049aad1/src/utils.c) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量) |
| [HAMi-core c0f9da6 — 原始碼](https://github.com/Project-HAMi/HAMi-core/tree/c0f9da6e14c0841039c9c89622a3f442d3bf10de) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#classic-hami-的接入路徑) |
| [k8s-dra-driver v0.1.0 — deploy/container/Dockerfile](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/deploy/container/Dockerfile) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#函式庫身分與原始碼的界線) |
| [k8s-dra-driver v0.1.0 — driver 功能開關](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/pkg/featuregates/featuregates.go) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#dra-如何取代-classic-控制元件) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L184-L244）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L184-L244) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#記憶體配置如何計量)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#sm-比例如何節流)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L196-L281）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L196-L281) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#dra-如何取代-classic-控制元件) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L214-L244）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L214-L244) | [04-isolation/README.md](../../book/04-isolation/README.md#三層隔離模型) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L248-L275）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L248-L275) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#classic-hami-的接入路徑)、[04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#dra-如何取代-classic-控制元件)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L50-L111）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L50-L111) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#dra-如何取代-classic-控制元件)、[04-isolation/hami-vs-mps.md](../../book/04-isolation/hami-vs-mps.md#兩種路徑的差異) |
| [k8s-dra-driver v0.1.0 — HAMi-core 注入與配置（L87-L111）](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/cmd/hami-kubelet-plugin/hami_core.go#L87-L111) | [04-isolation/results.md](../../book/04-isolation/results.md#記帳餘額不等於實體空閒量) |
| [k8s-dra-driver v0.1.0 — README.md](https://github.com/Project-HAMi/k8s-dra-driver/blob/v0.1.0/README.md) | [04-isolation/hami-dra.md](../../book/04-isolation/hami-dra.md#dra-如何取代-classic-控制元件) |

### PyTorch 與模型服務

| 參考資料 | 書中使用位置 |
| --- | --- |
| [python_backend r24.10 — Triton Python backend r24.10 的多 instance 實作說明](https://raw.githubusercontent.com/triton-inference-server/python_backend/r24.10/README.md) | [04-isolation/mps.md](../../book/04-isolation/mps.md#解讀) |
| [pytorch v2.5.1 — CUDA 快取配置器](https://raw.githubusercontent.com/pytorch/pytorch/v2.5.1/c10/cuda/CUDACachingAllocator.cpp) | [03-oom/README.md](../../book/03-oom/README.md#為何關閉-pytorch-記憶體快取) |
| [pytorch v2.5.1 — docs/source/notes/cuda.rst（memory-management）](https://github.com/pytorch/pytorch/blob/v2.5.1/docs/source/notes/cuda.rst#memory-management) | [03-oom/README.md](../../book/03-oom/README.md#為何關閉-pytorch-記憶體快取) |
| [vLLM 的平行部署](https://docs.vllm.ai/en/latest/serving/parallelism_scaling/) | [04-isolation/mps.md](../../book/04-isolation/mps.md#解讀) |

### Kubeflow

| 參考資料 | 書中使用位置 |
| --- | --- |
| [KFP commit 8279bb0 — 固定原始碼 archive](https://codeload.github.com/kubeflow/pipelines/tar.gz/8279bb0cd394628c77d28a4c28174334e57ddb4f) | [06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| [KFP v2beta1 參考](https://www.kubeflow.org/docs/components/pipelines/reference/api/kubeflow-pipeline-api-spec/) | [06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#直接用-rest-提交) |
| [KFP 的 DRA 整合說明](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/dynamic-resource-allocation/) | [06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#背景原理) |
| [pipelines 8279bb0 — SDK wheel 建置 hook](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/hatch_build.py) | [06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#需要修改-pipeline-時重建-sdk) |
| [pipelines 8279bb0 — Task ResourceClaim SDK](https://github.com/kubeflow/pipelines/blob/8279bb0cd394628c77d28a4c28174334e57ddb4f/sdk/python/kfp/kubernetes/pod_resource_claim.py) | [06-heterogeneous/kubeflow.md](../../book/06-heterogeneous/kubeflow.md#task-所用的-template-與原始碼) |

### 硬體規格

| 參考資料 | 書中使用位置 |
| --- | --- |
| [NVIDIA A40 — 官方規格表](https://images.nvidia.com/content/Solutions/data-center/a40/nvidia-a40-datasheet.pdf) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#本書為什麼選這些方法) |
| [NVIDIA GeForce RTX 4090 — 官方規格](https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#本書為什麼選這些方法) |

### NVIDIA GPU 管理與虛擬化

| 參考資料 | 書中使用位置 |
| --- | --- |
| [gpu-operator v26.7.0 — assets/state-operator-validation/0500_daemonset.yaml](https://github.com/NVIDIA/gpu-operator/blob/v26.7.0/assets/state-operator-validation/0500_daemonset.yaml) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#背景原理) |
| [NVIDIA k8s-device-plugin v0.17.1 — GPU Feature Discovery 節點標籤](https://github.com/NVIDIA/k8s-device-plugin/blob/v0.17.1/README.md#deploying-with-gpu-feature-discovery-for-automatic-node-labels) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#屬性如何間接進入排程) |
| [NVIDIA k8s-device-plugin v0.17.1 — GPU 共用](https://github.com/NVIDIA/k8s-device-plugin/blob/v0.17.1/README.md#shared-access-to-gpus) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#整數資源代表什麼) |
| [NVIDIA AI Enterprise — MIG-Backed vGPU](https://docs.nvidia.com/ai-enterprise/release-7/latest/infra-software/vgpu/features/mig-backed-vgpu.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#背景原理)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [NVIDIA ComputeDomain 說明](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/26.3/dra-intro-install.html#overview) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#安裝命令與參數) |
| [NVIDIA GPU Operator — GPU Sharing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html) | [01-why-dra/device-plugin-limits.md](../../book/01-why-dra/device-plugin-limits.md#部署與設定)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [NVIDIA GPU Operator — Helm chart repository](https://nvidia.github.io/gpu-operator) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#使用原本的-helm-參數) |
| [NVIDIA MIG User Guide — Introduction](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/introduction.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#背景原理)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [NVIDIA MIG User Guide — Supported GPUs](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#本書為什麼選這些方法)、[02-deploy/verify.md](../../book/02-deploy/verify.md#先讀部署盤點) |
| [NVIDIA NGC — Helm chart repository](https://helm.ngc.nvidia.com/nvidia)（Helm repository 端點，供 `helm repo add` 使用；瀏覽器直接開啟會顯示 404） | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#確認-chart-來源) |
| [NVIDIA vGPU — Introduction to Licensing](https://docs.nvidia.com/vgpu/latest/grid-licensing-user-guide/intro-to-grid-licensing.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| [NVIDIA vGPU — Supported GPUs](https://docs.nvidia.com/vgpu/gpus-supported-by-vgpu.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#本書為什麼選這些方法) |
| [NVIDIA vGPU — Virtual Workstation Overview](https://docs.nvidia.com/vgpu/sizing/virtual-workstation/latest/overview.html) | [01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#背景原理)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#共用方法比較)、[01-why-dra/gpu-sharing-landscape.md](../../book/01-why-dra/gpu-sharing-landscape.md#本書為什麼選這些方法) |
| [NVIDIA — 26.7 安裝說明](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/26.7/dra-intro-install.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#使用原本的-helm-參數) |
| [NVIDIA — GPU Operator 26.3 的獨立 DRA 安裝範例](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/26.3/dra-intro-install.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#標示-dra-節點) |
| [NVIDIA — GPU Operator 元件介紹](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/index.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#背景原理)、[02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#各元件的責任) |
| [NVIDIA — GPU Operator 疑難排解](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/26.3/troubleshooting.html#gpu-operator-pods-are-stuck-in-init) | [02-deploy/node-prep.md](../../book/02-deploy/node-prep.md#停用-nouveau)、[02-deploy/troubleshooting.md](../../book/02-deploy/troubleshooting.md#背景原理) |
| [NVIDIA — 一般與預先編譯驅動容器](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/precompiled-drivers.html) | [02-deploy/node-prep.md](../../book/02-deploy/node-prep.md#背景原理) |
| [NVIDIA 驅逐協調要求](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/26.3/dra-intro-install.html#prerequisites) | [02-deploy/dra-driver.md](../../book/02-deploy/dra-driver.md#限制) |

### 節點部署與工具

| 參考資料 | 書中使用位置 |
| --- | --- |
| [Cilium netkit 條件](https://docs.cilium.io/en/stable/operations/performance/tuning/#netkit-mode) | [02-deploy/node-prep.md](../../book/02-deploy/node-prep.md#通過條件) |
| [manpages.ubuntu.com — lspci 選項](https://manpages.ubuntu.com/manpages/noble/man8/lspci.8.html) | [02-deploy/node-prep.md](../../book/02-deploy/node-prep.md#確認硬體可見) |
| [NFD 架構](https://kubernetes-sigs.github.io/node-feature-discovery/master/get-started/introduction.html) | [02-deploy/gpu-operator.md](../../book/02-deploy/gpu-operator.md#各元件的責任) |
| [Ubuntu Secure Boot 與模組簽章](https://documentation.ubuntu.com/security/security-features/platform-protections/secure-boot/) | [02-deploy/troubleshooting.md](../../book/02-deploy/troubleshooting.md#secure-boot) |
| [Ubuntu — 套件 archive 來源](http://archive.ubuntu.com/ubuntu/) | [02-deploy/troubleshooting.md](../../book/02-deploy/troubleshooting.md#dns-與-egress) |

## 重點整理

技術結論應連到能直接佐證它的 API 說明、固定版本原始碼或實驗證據。閱讀參考資料時，請一併核對各章的測試條件與限制，尤其不要把 HAMi-core 參考 commit、歷史 MPS 案例或其他版本的安裝指南當成原叢集實測。

<!-- nav:start -->
[← 上一頁：附錄 C　復現對照表](reproduction-map.md) ｜ [目錄](../README.md)
<!-- nav:end -->
