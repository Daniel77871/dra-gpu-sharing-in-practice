# HAMi 配額範圍的原始資料與摘要

`raw/` 保存兩個容器共用一個 Pod、兩個 Pods 共用一份 Claim，以及兩個 Pods 使用獨立 Claims 的配額範圍測試。[來源清單](SOURCES.md)列出原始路徑與行號範圍，[SHA256 清單](source-sha256.json)辨識輸入檔案。前兩種條件來自第一段執行，獨立 Claims 來自後續執行；停滯輪次的日誌與退出狀態保留其另行復原來源。

`summary.json` 與 `summary.csv` 由[HAMi 分析程式](../../labs/04-isolation/common/analysis/hami.py)重算。`trial.json` 保留實驗參數、hold 時間、錯誤與清理狀態；原始已計算的 `result` 欄位未匯入。

`provider-evidence/` 保存[安裝後](provider-evidence/host-installed.json)與[還原清理後](provider-evidence/host-restored-clean.json)的 host 路徑、裝置／inode、檔案大小與 SHA256。三個 HAMi 路徑在清理後都不存在；原有 NVIDIA 管理用 CDI 檔案仍保留相同身分與雜湊。這些檔案不包含函式庫內容、checkpoint 內容或完整 CDI 設定；共用的 provider、DeviceClass 與 ResourceSlice 原始設定列在來源清單及 SHA256 清單中。

```sh
$ .venv/bin/python tools/extract_data.py e2b
$ python3 labs/04-isolation/common/analysis/hami.py data/e2b/raw --output /tmp/hami-scope-summary
```

分析命令會寫出九列摘要，並因保留的停滯輪次回傳退出碼 2。這表示資料裡存在未完成觀察；它不會把復原成功改寫成配額測試完成。

## 配置屏障與三種計量

`round-XX.json` 保留每個完整配置屏障的 client 身分、成功張量、CUDA total／free、配置回覆、NVML process 用量與原始整卡輸出。重算時由各 client 的張量與 host PID 加總，不採用來源檔案裡已計算的合計欄位；實際遇到 `OutOfMemoryError` 的配置嘗試才計入 CUDA OOM 次數，後續保留同一停止狀態不重複計數。

`tensor_total_gib` 是完成配額測試時兩個測量行程的成功張量合計。`nvml_process_total_mib` 是同一屏障的兩個行程實體用量合計，與整張 GPU 的已用量不同；容器內 CUDA 的 total／free 則是 HAMi 配額視圖。

`identity.json` 保留兩個 READY 記錄、Claim／Pod／容器到 host PID 的對應、libvgpu SHA256、載入 maps、注入環境變數與 cache 裝置／inode。cache 路徑相同與裝置／inode 相同各自列成欄位，兩者都不能單獨證明跨 Pod 的合計配額可靠。

## 停滯輪次

`case4a-r3` 的最後完整屏障是 `round-14.json`；下一步沒有完整觀察。這一列的 `tensor_total_gib`、`nvml_process_total_mib`、`cuda_oom_count` 與 `quota_exceeded` 保持空值，`outcome` 為 `stalled`，`complete=false`。最後完整屏障的數字只放在 `last_barrier_*`，不是最終最大值或配額上限。

`recovery/terminated-pods.json` 保存另行復原後的容器終止狀態。復原的非零退出與 `Error` 不會被改成 CUDA OOM 或 Kubernetes `OOMKilled`；停滯的內部原因仍未證明。完整結果見[書中的配額範圍測試](../../book/04-isolation/hami-dra.md#結果)。
