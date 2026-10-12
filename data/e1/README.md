# 共用 GPU OOM 的原始資料

`raw/` 保存 exit 與 hold 各三輪的 A／B JSON Lines 日誌、整顆 GPU 取樣、階段時間與 Claim。`trial.json` 補上分析所需的實際終止時間、exit code 與觀察期；兩個 `*-final.json` 只保留核對終止狀態與 image digest 的欄位。

日誌、GPU 取樣、`phases.json` 與 `summary-original.csv` 維持原始 bytes。Claim 去除非必要 metadata；Pod 快照去除 annotations、container ID、網路位址與其他無關欄位。`summary-original.csv` 是原始對照檔，保留原措辭；重算版本 `summary.csv` 移除文字判讀欄位，所有數值語意相同。實際讀取的來源逐一列在 [SOURCES.md](SOURCES.md)。

在儲存庫根目錄重算：

```sh
$ python3 labs/03-oom/analysis/analyze.py data/e1/raw --output data/e1
```

`SUMMARY.md` 與第 3 章使用同一個表格函式。恢復秒數與 hold p95 倍率顯示至小數三位，CSV／JSON 保留未四捨五入的值；圖表直接讀取原始資料並呼叫同一份分析程式。

如需重新從實驗室來源擷取，執行以下命令，再執行上面的分析：

```sh
$ .venv/bin/python tools/extract_data.py e1
$ .venv/bin/python tools/make_figures.py --only 03-oom
```

擷取器只開啟明列的六輪檔案，不讀取命令封存、inventory 或其他實驗資料。[第 3 章](../../book/03-oom/README.md)解釋機制與限制，[重現套件](../../labs/03-oom/README.md)說明自己的執行資料。
