# 安装清单



# 启动清单

## Linux

### 1.启动PostgrSQL

```bash
sudo pg_ctlcluster 14 main start

# 确认服务状态
sudo pg_ctlcluster 14 main status
```

### 2.启动vLLM

```bash
cd Bidding_QA_Chatbot

source .venv/bin/activate

# 修复 OMP_NUM_THREADS 警告
# 或设置为 CPU 核心数，如 8
export OMP_NUM_THREADS=16

# 启动服务
vllm serve /root/autodl-tmp/DeepSeek-R1-0528-Qwen3-8B \
    --trust-remote-code \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len 36000
  
# 查看显存
nvidia-smi
```

### 3.启动Ollama

```bash
ollama serve
```

### 4.启动后端

```bash
source .venv/bin/activate

python main.py api
```

### 5.启动前端

```
cd frontend && npm run dev
```

### 6.打开AutoDL SSH隧道工具

```
代理到本地端口：3000
```

