# 附錄 C　重現對照表

## 本頁要回答的問題

每個章節的重現套件、整理資料與原始證據如何對應？本頁也列出在原叢集執行縮短版的日期與驗證範圍，讓讀者分辨正式量測與套件驗證。

## 背景原理

正式實驗的衍生資料在 `data/`，可供離線重建表格與圖表；重現套件的新輸出在 `labs/<lab>/out/<run-id>/`，不納入版本控制。實機驗證紀錄記錄縮短版實際執行結果，不把縮短參數的絕對數字當成原正式量測。[資料索引](../../data/README.md)、[重現實驗指南](../../labs/README.md)

## 結果

表中「原始證據」是原始實驗室 `dra-gpu-oom-lab` 內的相對路徑，僅說明來源，不是本儲存庫的檔案連結。每一列的細部來源、雜湊與擷取規則可在整理資料的 `SOURCES.md` 中查閱。

| 章節或實驗 | 重現套件 | 整理資料 | 原始證據 | 實機驗證日期與結果 |
| --- | --- | --- | --- | --- |
| [第 1 章　為何需要 DRA](../01-why-dra/README.md) | 原理章；從[部署套件](../../labs/02-setup/README.md)驗證端到端流程 | 無獨立量測資料 | 官方說明與固定版本原始碼，見[參考資料](references.md) | 不另列實驗；部署驗證見下一列 |
| [第 2 章　部署](../02-deploy/README.md) | [02-setup](../../labs/02-setup/README.md) | [e1 環境版本](../../data/e1/SOURCES.md) | 作者安裝筆記；`results/a40-20261006T062717Z/version.json` | [2026-10-11：A40 與 RTX 4090 的 CUDA 工作、配置與回收通過](../../labs/02-setup/VERIFIED.md) |
| [第 3 章　共用 OOM](../03-oom/README.md) | [03-oom](../../labs/03-oom/README.md) | [e1](../../data/e1/SOURCES.md) | `results/a40-20261006T062717Z/` 的 exit／hold 輪次、日誌與 GPU 取樣 | [2026-10-11：hold 縮短版重現配置失敗及釋放後恢復，修正收集與清理後重跑通過](../../labs/03-oom/VERIFIED.md) |
| [第 4 章　MPS](../04-isolation/mps.md) | [04-isolation/mps](../../labs/04-isolation/mps/README.md) | [e2](../../data/e2/SOURCES.md) | `results/experiment-2-isolation/mps-20261006T092100Z/` | [2026-10-12：single-process 與同一 Pod 內的 dual-process 通過，確認 process 配額與合計超額的差異](../../labs/04-isolation/VERIFIED.md) |
| [第 4 章　ConsumableShares](../04-isolation/consumable-shares.md) | [04-isolation/consumable-shares](../../labs/04-isolation/consumable-shares/README.md) | [e2](../../data/e2/SOURCES.md) | `results/experiment-2-isolation/e2-capacity-20261007T015625Z/` 的 NVIDIA 容量與 runtime 案例 | [2026-10-12：不足時 Pending、釋放後配置及 runtime 無配額限制通過；provider 已還原](../../labs/04-isolation/VERIFIED.md) |
| [第 4 章　HAMi-DRA 與範圍](../04-isolation/hami-dra.md) | [04-isolation/hami-dra](../../labs/04-isolation/hami-dra/README.md) | [e2/hami](../../data/e2/hami/SOURCES.md)、[e2b](../../data/e2b/SOURCES.md) | `results/experiment-2-isolation/e2-capacity-20261007T015625Z/`；`results/experiment-2b-hami-scope/e2b-20261007t053126z/` 與 `e2b-20261007t060500z/` | [2026-10-12：帳本、同一 container 內的配額與跨 Pod 合計反例重現](../../labs/04-isolation/VERIFIED.md)；[修正後解除安裝在效能縮短版通過](../../labs/05-performance/VERIFIED.md#hami-模式) |
| [第 5 章　效能](../05-performance/README.md) | [05-performance](../../labs/05-performance/README.md) | [e3](../../data/e3/SOURCES.md) | `deliveries/experiment-3-completion-20261007-v3/EXPERIMENT_3_PERFORMANCE.md` 及 `results/` | [2026-10-12：NVIDIA 模式與 HAMi-DRA 重現同步間歇工作的主要趨勢，provider 設定、切換與還原通過](../../labs/05-performance/VERIFIED.md) |
| [第 6 章　異質 GPU 與 KFP](../06-heterogeneous/README.md) | [06-heterogeneous](../../labs/06-heterogeneous/README.md) | [e4](../../data/e4/SOURCES.md)、[e4b](../../data/e4b/SOURCES.md) | `results/experiment-4/e4-20261011t0035z/`、`results/experiment-4b/e4b-20261011t060100z/`；對應 `deliveries/experiment-4-e4-20261011t0035z/`、`deliveries/experiment-4b-e4b-20261011t060100z/` | [2026-10-12：原生選卡、firstAvailable 偏好與替代、HAMi-DRA 兩卡容量選擇、KFP 的 H1／H2／L1 管線及 provider 還原通過](../../labs/06-heterogeneous/VERIFIED.md) |

## 重點整理

離線查核使用正式整理資料，套件可用範圍則查實機驗證紀錄。開始新的叢集執行前，請核對[版本與 image](versions.md)及各套件前置條件；需要切換 provider 或安裝 KFP 的流程，還必須保存與驗證還原證據。

<!-- nav:start -->
[← 上一頁：附錄 B　版本與 container image](versions.md) ｜ [目錄](../README.md) ｜ [下一頁：附錄 D　參考資料 →](references.md)
<!-- nav:end -->
