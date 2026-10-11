# 第 1 章　為什麼需要 DRA

## 本章要回答的問題

向 Kubernetes 要一張 GPU，和要求幾個工作安全地共用 GPU，是不同的問題。傳統 Device Plugin 把裝置轉成可計數的 extended resources；DRA（Dynamic Resource Allocation）則讓需求、裝置條件、驅動設定與配置結果成為可追蹤的 API 物件。[Kubernetes DRA 概觀](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/)

本章先建立後續實驗需要的心智模型，回答以下問題：

- 為什麼 `nvidia.com/gpu: 1` 不足以描述異質 GPU 與共用需求？
- DRA 如何讓排程器依裝置屬性、候選優先序與容量需求選取資源？
- TimeSlicing、MPS、MIG、vGPU、HAMi-core 與 ConsumableShares 各自在哪一層作用？本書測試哪些路徑？

## 本章地圖

| 頁面 | 閱讀後應掌握的事 |
|---|---|
| [1.1 Device Plugin 的限制](device-plugin-limits.md) | 從 kubelet 的註冊與配置流程，理解整數資源模型為何難以表達選卡、共用與容量。 |
| [1.2 DRA 的資源模型與配置流程](dra-model.md) | 用實際 DeviceClass、ResourceSlice 與 ResourceClaim，追蹤需求如何走到容器，以及資源如何回收。 |
| [1.3 GPU 共用技術地圖](gpu-sharing-landscape.md) | 比較各種隔離的作用層級與邊界，理解本書的實測範圍。 |

## 背景原理

閱讀本書時，請持續分開三件事：**能否排程、能用多少、故障會影響誰**。Claim 能共用，表示多個 Pod 可引用同一筆裝置配置；容量能記帳，表示排程器可檢查請求總量。至於 CUDA 行程能否超額使用 VRAM，仍取決於驅動或使用者空間的執行期機制。[DRA consumable capacity 設計](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5075-dra-consumable-capacity/README.md)

```mermaid
flowchart LR
  R["工作需求"] --> S["排程與容量記帳"]
  S --> P["裝置準備"]
  P --> E["CUDA 執行"]
  E --> O["用量與故障觀測"]
```

這條閱讀路徑會延伸到[第 3 章的 OOM](../03-oom/README.md)、[第 4 章的隔離](../04-isolation/README.md)、[第 5 章的效能](../05-performance/README.md)與[第 6 章的異質選卡](../06-heterogeneous/README.md)。本章先解釋模型，部署與實際操作由[第 2 章](../02-deploy/README.md)開始。

## 重點整理

- Device Plugin 的基本契約是裝置數量與容器存取設定，裝置條件的表達能力有限。
- DRA 把裝置清單、分類、需求與配置結果分開，讓排程器能看見裝置層級的條件。
- ResourceClaim 可由多個 Pod 共用；一般 Pod 引用 ResourceClaimTemplate 時，則各自產生 Claim。
- 容量記帳、執行期配額與故障隔離需要分別確認。
- 本書實測 TimeSlicing、MPS、HAMi-DRA 與 NVIDIA ConsumableShares；MIG 與 vGPU 用來說明技術邊界。

<!-- nav:start -->
[← 上一頁：前言：為什麼要研究 GPU 共用](../00-preface.md) ｜ [目錄](../README.md) ｜ [下一頁：1.1 Device Plugin 的限制 →](device-plugin-limits.md)
<!-- nav:end -->
