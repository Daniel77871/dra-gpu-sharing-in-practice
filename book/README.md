# Kubernetes DRA GPU 共用實戰

本書從 Kubernetes DRA 的資源模型與部署開始，整理 GPU 共用時的 OOM、隔離、效能與異質 GPU 選卡實驗。各章將連結部署設定、量測結果與原始證據，保留實驗的限制。

本書面向熟悉 Kubernetes、尚未接觸這組實驗的工程師。部署與實驗章節對應 `labs/` 下的復現套件；執行方式與共同前提見[復現實驗指南](../labs/README.md)。目前除本頁外皆為章節骨架。

## 目錄

<!-- toc:start -->
- [前言：為什麼要研究 GPU 共用](00-preface.md)
- [第 1 章　為什麼需要 DRA](01-why-dra/README.md)
  - [1.1 Device Plugin 的限制](01-why-dra/device-plugin-limits.md)
  - [1.2 DRA 的資源模型與配置流程](01-why-dra/dra-model.md)
  - [1.3 GPU 共用技術地圖](01-why-dra/gpu-sharing-landscape.md)
- [第 2 章　部署 GPU Operator 與 NVIDIA DRA Driver](02-deploy/README.md)
  - [2.1 GPU 節點準備](02-deploy/node-prep.md)
  - [2.2 安裝 GPU Operator](02-deploy/gpu-operator.md)
  - [2.3 安裝 NVIDIA DRA Driver](02-deploy/dra-driver.md)
  - [2.4 驗證與第一個 GPU Job](02-deploy/verify.md)
  - [2.5 疑難排解](02-deploy/troubleshooting.md)
- [第 3 章　共用 GPU 時的 OOM 行為](03-oom/README.md)
- [第 4 章　防止 OOM：三層隔離模型](04-isolation/README.md)
  - [4.1 ConsumableShares：排程記帳](04-isolation/consumable-shares.md)
  - [4.2 MPS：每個行程的 VRAM 上限](04-isolation/mps.md)
  - [4.3 HAMi-DRA：攔截 CUDA 呼叫的配額](04-isolation/hami-dra.md)
  - [4.4 HAMi 與 MPS 的差異](04-isolation/hami-vs-mps.md)
  - [4.5 三種方案的比較與容量觀測](04-isolation/results.md)
- [第 5 章　共用的排程機制與效能](05-performance/README.md)
- [第 6 章　只描述需求，讓排程器挑 GPU](06-heterogeneous/README.md)
  - [6.1 DRA 的選卡機制](06-heterogeneous/selection.md)
  - [6.2 與 Kubeflow Pipelines 整合](06-heterogeneous/kubeflow.md)
  - [6.3 異質 GPU 選卡實驗](06-heterogeneous/experiments.md)
- [第 7 章　結論與實務建議](07-conclusion.md)
- [附錄 A　名詞對照表](appendix/glossary.md)
- [附錄 B　版本與映像檔](appendix/versions.md)
- [附錄 C　復現對照表](appendix/reproduction-map.md)
- [附錄 D　參考資料](appendix/references.md)
<!-- toc:end -->

## 如何閱讀

- 先閱讀 DRA 的資源模型，再依部署章節準備實驗環境。
- 依問題選擇 OOM、隔離、效能或異質 GPU 章節，搭配對應的 `labs/` 套件。
- 執行前確認該實驗的前提與清理步驟，並參考記錄實機驗證結果的 `VERIFIED.md`。
- 以附錄查找名詞、版本及復現對照。

<!-- nav:start -->
[目錄](README.md) ｜ [下一頁：前言：為什麼要研究 GPU 共用 →](00-preface.md)
<!-- nav:end -->
