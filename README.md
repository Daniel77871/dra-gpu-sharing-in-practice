# Kubernetes DRA GPU 共用實戰

This repository contains a Traditional Chinese technical book and runnable labs on GPU sharing with Kubernetes DRA, NVIDIA MPS, HAMi-DRA, and TimeSlicing. It separates scheduling accounting, runtime quotas, and physical GPU memory usage, compares throughput and latency, and tests requirement-based allocation across heterogeneous GPUs. The book includes measured results, source references, and records of shortened lab runs on the original cluster.

這是一套寫給 Kubernetes 工程師的 GPU 共用實驗紀錄與復現套件。從[書籍目錄](book/README.md)可依序閱讀 DRA 模型、部署、OOM、隔離、效能與異質 GPU 選卡；每個實驗章節都連到完整設定、整理資料和實機驗證紀錄。方案的能力與限制見[前言結論速覽](book/00-preface.md#結論速覽)及[決策指南](book/07-conclusion.md#依需求選擇方案)。

## 儲存庫結構

| 路徑 | 內容 |
| --- | --- |
| [book/](book/README.md) | 技術書、章節導覽、名詞、版本與參考資料 |
| [labs/](labs/README.md) | 部署與實驗套件、工作負載、收集與清理腳本、實機驗證紀錄 |
| [data/](data/README.md) | 從原始實驗整理的結果、輸入設定與來源清單 |
| [book/images/](book/images/) | 由整理資料產生的 SVG 圖表 |
| [tools/](tools/) | 資料擷取、圖表、導覽與內容檢查工具 |
| [tests/](tests/) | 離線分析、manifest 渲染與套件行為檢查 |

## 快速開始

閱讀請從[前言](book/00-preface.md)開始，或依[復現對照表](book/appendix/reproduction-map.md)選擇問題。執行 lab 前先閱讀[labs 使用說明](labs/README.md)與該套件的 README；完整模式與縮短版、所需權限、收集和還原步驟都在套件內。

例如，完成 GPU 部署後，可依 [OOM 套件](labs/03-oom/README.md)的前置條件執行縮短版，再核對[實機驗證紀錄](labs/03-oom/VERIFIED.md)：

```sh
$ cd labs/03-oom
$ SMOKE=1 bash scripts/run.sh smoke-oom
$ bash scripts/collect.sh smoke-oom
$ bash scripts/cleanup.sh smoke-oom
```

離線檢查從儲存庫根目錄執行，不會連到叢集。建立 Python 環境並安裝固定相依套件後，執行以下命令；`make_figures.py` 會重新產生圖表，不以不同機器的 SVG 位元差異判斷結果。

```sh
$ python3 -m venv .venv
$ .venv/bin/python -m pip install -r requirements.txt
$ .venv/bin/python tools/build_nav.py --check
$ .venv/bin/python -m pytest -q
$ .venv/bin/python tools/check_book.py
$ .venv/bin/python tools/extract_references.py --check
$ .venv/bin/python tools/scan_sensitive.py
$ .venv/bin/python tools/make_figures.py
```

## 使用需求

| 用途 | 需求 |
| --- | --- |
| 閱讀 | GitHub 或可顯示 Markdown、Mermaid 與 SVG 的閱讀環境 |
| 離線檢查與圖表 | Python 3.12、[requirements.txt](requirements.txt)；Noto CJK 字型；kustomize v5.4.3 放在 `.work/bin/kustomize` 以執行渲染測試 |
| 執行 lab | 完整 clone；可存取實驗叢集的 `kubectl`、Bash、Python 3，以及各套件列出的讀取、建立、exec 與清理權限 |
| GPU 環境 | 原始部署的 Kubernetes、GPU Operator、driver 與 DRA 版本見[版本表](book/appendix/versions.md)；其他版本需自行驗證 |

CI 的工具安裝與檢查方式見[工作流程](.github/workflows/check.yml)。需要切換 provider 或安裝 KFP 的實驗，使用各套件明列的獨立備份、驗證與還原步驟；先讀完套件說明再執行。

## 資料來源

`data/` 是原始實驗室 `dra-gpu-oom-lab` 的衍生資料，並非新的量測。各資料夾的 `SOURCES.md` 保存來源路徑、雜湊與整理規則；[資料索引](data/README.md)與[復現對照表](book/appendix/reproduction-map.md)連結到使用它們的章節。擷取入口是 [tools/extract_data.py](tools/extract_data.py)，重新擷取需要另外取得原始實驗資料；閱讀、圖表產生與一般離線檢查使用儲存庫內的整理資料即可。

本儲存庫以 [MIT License](LICENSE) 授權。
