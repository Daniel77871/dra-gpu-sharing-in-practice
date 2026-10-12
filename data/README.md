# 實驗資料索引

本目錄保存從原始實驗室 `dra-gpu-oom-lab` 擷取的衍生資料。各資料夾的 `SOURCES.md` 記錄完整來源路徑、SHA256 與整理方式；以下來源皆是原始實驗室內的相對路徑，原始檔案不隨本儲存庫提供。

| 資料夾 | 內容 | 原始來源 | 使用章節 |
| --- | --- | --- | --- |
| [e1](e1/SOURCES.md) | OOM 各輪摘要、workload 事件、GPU 取樣與環境版本 | `results/a40-20261006T062717Z/` | [第 3 章](../book/03-oom/README.md)；環境版本亦供[第 2 章](../book/02-deploy/README.md)使用 |
| [e2](e2/SOURCES.md) | MPS 配額、dual-process、容量記帳與 runtime 反例；另含來源已明列的歷史 MPS 案例資料 | `results/experiment-2-isolation/mps-20261006T092100Z/`、`results/experiment-2-isolation/e2-capacity-20261007T015625Z/`；歷史案例來源見 `SOURCES.md` | [第 4 章](../book/04-isolation/README.md)、[MPS 原理與結果](../book/04-isolation/mps.md) |
| [e2/hami](e2/hami/SOURCES.md) | HAMi-DRA 的 memory／cores 帳本、single-process 與同一 container 內的 dual-process 結果 | `results/experiment-2-isolation/e2-capacity-20261007T015625Z/` 中的 HAMi 案例 | [HAMi-DRA 配額](../book/04-isolation/hami-dra.md) |
| [e2b](e2b/SOURCES.md) | HAMi-DRA 跨 container、共用 Claim 與獨立 Claim 的範圍比較 | `results/experiment-2b-hami-scope/e2b-20261007t053126z/`、`results/experiment-2b-hami-scope/e2b-20261007t060500z/` | [HAMi-DRA 範圍實驗](../book/04-isolation/hami-dra.md#跨-container-與跨-pod-的配額範圍) |
| [e3](e3/SOURCES.md) | 固定工作量、配對矩陣、批次摘要與效能分析輸出 | `deliveries/experiment-3-completion-20261007-v3/EXPERIMENT_3_PERFORMANCE.md` 及同目錄的 `results/` | [第 5 章](../book/05-performance/README.md) |
| [e4](e4/SOURCES.md) | 屬性選卡、Template、共用與釋放的驗收資料 | `results/experiment-4/e4-20261011t0035z/`；`deliveries/experiment-4-e4-20261011t0035z/` 的結果表 | [第 6 章](../book/06-heterogeneous/README.md) |
| [e4b](e4b/SOURCES.md) | 統一容量、Pending 後再配置、KFP 與生命週期的驗收資料 | `results/experiment-4b/e4b-20261011t060100z/`；`deliveries/experiment-4b-e4b-20261011t060100z/` 的摘要與 CSV | [第 6 章](../book/06-heterogeneous/README.md) |

[tools/extract_data.py](../tools/extract_data.py) 是資料擷取入口；原始實驗資料的位置以 `--lab` 或環境變數 `DRA_LAB_DIR` 指定（預設為與本儲存庫並列的 `../dra-gpu-oom-lab`），例如 `python3 tools/extract_data.py --lab /path/to/dra-gpu-oom-lab e1`。圖表由 [tools/make_figures.py](../tools/make_figures.py) 讀取本目錄的資料後產生，縮短版重現結果則保存在各 lab 的 `out/` 與[實機驗證紀錄](../book/appendix/reproduction-map.md)，不覆寫原始正式量測資料。
