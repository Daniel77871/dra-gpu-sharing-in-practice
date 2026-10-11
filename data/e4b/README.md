# E4B 資料

`trials.json` 分別保留原始正式狀態與後續離線判讀，正式 invalid 的紀錄不會因重新判讀成功而變成正式通過輪次。`acceptance.json` 保留原始驗收摘要與能力清單，`task-results.json` 保留配置位置、Pod UID、Claim UID、退出狀態與時間；未取得的時間保持 null。

`lifecycle.json` 保留 Claim 建立、配置、刪除的觀測事件與所屬 Pod UID，並保留 KFP Run 和 Task API 的各自狀態。`capacity-samples.csv` 保留每個配對取樣窗與有正負號的 NVML offset；DRA remaining 和 Physical free 不是原子快照。容量表依最高 reserved memory 選取，額度相同時取最高 physical used，差值方向為 Physical free 減 DRA remaining。

從完整儲存庫執行分析：

```bash
$ python3 labs/06-heterogeneous/analysis/analyze.py --e4 data/e4 --e4b data/e4b
```

[SOURCES.md](SOURCES.md) 列出擷取器實際讀取的來源，對應原始內容的 SHA256、行數與位元組數在 [source-hashes.json](source-hashes.json)。Claim 名稱變動的內部原因仍未釐清；完整 Pod UID 和 Claim UID 的刪除證據用來核對生命週期，不以名稱變化推論洩漏。

詳見[異質 GPU 實驗結果](../../book/06-heterogeneous/experiments.md)。
