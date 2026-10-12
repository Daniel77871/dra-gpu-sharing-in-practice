# Manifest 來源

Claim 的裝置選擇與 GpuConfig，以及 Job 的 workload 設定，來自下列完整部署記錄。重現版本改為 Job、固定角色名稱與書籍 label；控制檔改用 emptyDir，由不建立 CUDA context 的 init container 建立 owner。Sequential 與 HAMi 的獨立 Claim 由每個 Pod 的 ResourceClaimTemplate 產生；前者逐個執行並釋放，後者同時保留各自的容量額度。

- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/exclusive/b2-continuous-exclusive-n1/pod-task-0-input.yaml` → `yaml-full/11a64fe390a1935869948bafddf1f1b05bedf5f1a27cda29cb3845d2acb28e53.md`
- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/exclusive/b2-continuous-exclusive-n1/resourceclaim-gpu-0-input.yaml` → `yaml-full/f2a5e73a78c7936514ec00b3e7bed5f6c298de388dd4287e1d0b5d4f051cb8c4.md`
- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/sequential/b2-continuous-sequential-n2/serial-task-0/resourceclaim-gpu-0-input.yaml` → `yaml-full/b769413affe792a789e8daa8b773d2baeb1e0f49daafc55ecfa7a194cf121f29.md`
- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/timeslicing/b2-continuous-timeslicing-n2/resourceclaim-gpu-0-input.yaml` → `yaml-full/6ab32cca63f66f6040f4aa834277cf51d1a7093a4b4b76b52126eaed8cf5300e.md`
- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/mps/b2-continuous-mps-n2/resourceclaim-gpu-0-input.yaml` → `yaml-full/2a3175056340f37e56a07d9dde095f65d55f43aa667a4313c6e226186608c968.md`
- `results/experiment-3-performance/a40/20261007T151804Z/formal/continuous/hami/b2-continuous-hami-n2/resourceclaim-gpu-0-input.yaml` → `yaml-full/a55899576a8e2fcc3b588b08aa39cb5fe1f0c28d435e6dbbc45743ac89ea9731.md`
