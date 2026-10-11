# Kubernetes DRA GPU 共用實戰

本書從 Kubernetes DRA 的需求與配置模型出發，說明 GPU 共用的部署、OOM、隔離、效能與異質選卡。實驗顯示，排程容量記帳、CUDA 執行期配額與 Pod 的實體 GPU 記憶體上限是不同的保證，不能互相替代。共用能否增加吞吐量取決於工作留下的空檔，仍須衡量單個工作的延遲。Task 可以描述屬性或容量需求並取得可行 GPU 配置，但本書的異質實驗沒有驗證效能最佳選卡。[隔離結果](04-isolation/results.md)、[效能解讀](05-performance/README.md#解讀)、[選卡限制](06-heterogeneous/README.md#限制)

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

本書面向熟悉 Kubernetes、尚未看過這組實驗的工程師。[前言](00-preface.md)整理研究問題、環境與結論速覽；每頁底部的上一頁與下一頁可帶你順序通讀，章節內的連結則可深入機制與完整設定。

| 閱讀目的 | 起點 |
| --- | --- |
| 建立可用的 GPU 叢集 | [DRA 模型](01-why-dra/README.md) → [部署](02-deploy/README.md) → [需求與異質選卡](06-heterogeneous/README.md) |
| 選擇共用與隔離方式 | [OOM](03-oom/README.md) → [三層隔離](04-isolation/README.md) → [效能](05-performance/README.md) → [決策指南](07-conclusion.md) |
| 核對或復現實驗 | [版本與映像檔](appendix/versions.md) → [復現對照表](appendix/reproduction-map.md) → 對應 lab 與結果表 |

## 復現套件與實機驗證

共同前提見[復現實驗指南](../labs/README.md)，各套件 README 包含完整模式、縮短版、收集與清理步驟。每個 lab 都在原叢集執行過縮短版，實際通過項目與補測狀態由實機驗證紀錄明列：[部署](../labs/02-setup/VERIFIED.md)、[OOM](../labs/03-oom/VERIFIED.md)、[隔離](../labs/04-isolation/VERIFIED.md)、[效能](../labs/05-performance/VERIFIED.md)及[異質 GPU](../labs/06-heterogeneous/VERIFIED.md)。正式結果與縮短版的證據用途不同，請先核對[復現對照表](appendix/reproduction-map.md)再比較數字。

<!-- nav:start -->
[目錄](README.md) ｜ [下一頁：前言：為什麼要研究 GPU 共用 →](00-preface.md)
<!-- nav:end -->
