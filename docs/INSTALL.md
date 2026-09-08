# ⚡ lightning-ocr — Installation Guide

Hardware-specific install + compile instructions for every supported platform.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Docker Installation](#docker-installation)
  - [CPU-only (any hardware)](#cpu-only-any-hardware)
  - [NVIDIA CUDA GPU](#nvidia-cuda-gpu)
  - [Intel GPU (SYCL/Vulkan)](#intel-gpu-syclvulkan)
  - [macOS Metal](#macos-metal)
- [Manual Installation](#manual-installation)
  - [Standalone (CPU)](#standalone-cpu)
  - [Standalone (NVIDIA GPU)](#standalone-nvidia-gpu)
- [NVIDIA GPU Deep Dive](#nvidia-gpu-deep-dive)
  - [VRAM Requirements by GPU](#vram-requirements-by-gpu)
  - [GPU Settings Cheat Sheet](#gpu-settings-cheat-sheet)
  - [Troubleshooting GPU Issues](#troubleshooting-gpu-issues)
  - [Compiling llama.cpp for CUDA](#compiling-llamacpp-for-cuda)
- [Post-Install Verification](#post-install-verification)
- [Configuration](#configuration)
  - [Security Configuration](#security-configuration)
  - [Environment Profiles](#environment-profiles)

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **CPU** | x86_64, 2+ cores | x86_64, 8+ cores |
| **RAM** | 4 GB | 16 GB |
| **Disk** | 10 GB free | 50 GB free (SSD) |
| **Docker** | 24.0+ with Compose v2.20+ | Latest |
| **Python** | 3.11 | 3.12 |
| **Tesseract** | 4.0+ | 5.0+ |

**For GPU:**
- **NVIDIA:** Driver 535+ with `nvidia-container-toolkit`
- **Intel:** oneAPI 2024+ or Vulkan drivers
- **macOS:** Apple Silicon M1/M2/M3/M4

---

## Docker Installation

### CPU-only (any hardware)

Works everywhere including Intel Core i5 2nd gen with 4 GB RAM.

```bash
# 1. Clone
git clone https://github.com/youruser/lightning-ocr.git
cd lightning-ocr

# 2. Create data directory
mkdir -p data

# 3. Start
docker compose up --build
```

**What this launches:**
- `lightning-ocr-app` — FastAPI + UI on port 8000
- `lightning-ocr-llama-cpu` — llama.cpp CPU server on port 8080 (auto-downloads GLM-OCR Q4_K_M)

**Open:**
- UI: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

---

### NVIDIA CUDA GPU

> **Important:** NVIDIA Container Toolkit must be installed on the host.

```bash
# 0. Verify GPU access works
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi

# 1. Clone
git clone https://github.com/youruser/lightning-ocr.git
cd lightning-ocr

# 2. Create data directory
mkdir -p data

# 3. Start with GPU acceleration
docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  up --build
```

**What changes with GPU compose:**
- Uses `ghcr.io/ggml-org/llama.cpp:server-cuda` — CUDA-enabled llama.cpp
- Requests all GPUs via `nvidia-container-runtime`
- Auto-detects VRAM and fits as many layers as possible
- Enables `--flash-attn` and `--mmproj-offload`

#### GPU Profiles

```bash
# 6 GB VRAM (GTX 1060, RTX 2060)
N_GPU_LAYERS=20 CTX_SIZE=2048 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

# 8 GB VRAM (RTX 2070, RTX 3060)
N_GPU_LAYERS=32 CTX_SIZE=4096 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

# 12 GB VRAM (RTX 3080, RTX 4070) — full offload
N_GPU_LAYERS=99 CTX_SIZE=8192 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

# 24 GB VRAM (RTX 3090, RTX 4090, RTX 5090) — maximum quality
N_GPU_LAYERS=99 CTX_SIZE=16384 CACHE_TYPE_K=f16 CACHE_TYPE_V=f16 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
```

> **Tip:** Set `N_GPU_LAYERS=auto` to let llama.cpp dynamically fit layers into available VRAM.

#### How to Find Optimal GPU Layers

```bash
# Run a test to see how many layers fit:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm llama \
  ./llama-server \
  -hf ggml-org/GLM-OCR-GGUF:Q4_K_M \
  --n-gpu-layers 99 2>&1 | grep "offloaded"

# Look for this output:
# "offloaded 32/32 layers to GPU"  ← ALL layers fit! Use N_GPU_LAYERS=32
# "offloaded 16/99 layers to GPU"  ← Only 16 fit, reduce N_GPU_LAYERS=16

# Monitor GPU during operation:
nvidia-smi -l 1
```

---

### Intel GPU (SYCL/Vulkan)

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.intel.yml \
  up --build
```

**Requirements:**
- Intel Arc A-series or UHD 730+ (11th Gen Intel or newer)
- Vulkan drivers installed on host
- `/dev/dri` device access

---

### macOS Metal

```bash
# macOS uses the CPU-only compose file — Metal acceleration is automatic
docker compose up --build

# Or for more control:
N_GPU_LAYERS=99 CTX_SIZE=8192 docker compose up
```

> **Note:** On Apple Silicon, llama.cpp auto-detects Metal backend. `N_GPU_LAYERS=99` offloads all layers to unified memory.

---

## Manual Installation

### Standalone (CPU)

```bash
# 1. System dependencies
sudo apt-get update && sudo apt-get install -y \
  tesseract-ocr \
  tesseract-ocr-eng \
  tesseract-ocr-osd \
  libgl1 \
  libglib2.0-0 \
  curl \
  libmagic1 \
  python3.11 python3.11-venv

# 2. Create virtualenv
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Copy and configure environment
cp .env.example .env
# Edit CPU settings in .env:
#   ACCEL_MODE=cpu
#   N_GPU_LAYERS=0
#   CTX_SIZE=2048
#   N_THREADS=4
#   FLASH_ATTN=false

# 5. Create data directory
mkdir -p data

# 6. Download GLM-OCR model (Q4_K_M — 3.8 GB, fits 4 GB RAM)
mkdir -p models
pip install huggingface-hub
huggingface-cli download ggml-org/GLM-OCR-GGUF Q4_K_M.gguf --local-dir ./models

# 7. Download and build llama.cpp
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_CPU=ON
cmake --build build --config Release -j$(nproc)
cd ..

# 8. Start llama.cpp server
./llama.cpp/build/bin/llama-server \
  -m ./models/Q4_K_M.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  --n-gpu-layers 0 \
  --ctx-size 2048 \
  --batch-size 256 \
  --threads 4 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --no-mmap

# 9. In another terminal, start lightning-ocr
source .venv/bin/activate
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --loop uvloop \
  --timeout-keep-alive 120
```

---

### Standalone (NVIDIA GPU)

```bash
# 1. System dependencies
sudo apt-get update && sudo apt-get install -y \
  tesseract-ocr \
  tesseract-ocr-eng \
  tesseract-ocr-osd \
  libgl1 \
  libglib2.0-0 \
  curl \
  libmagic1 \
  python3.11 python3.11-venv

# 2. Verify CUDA is installed
nvidia-smi
# Expected: CUDA Version: 12.x

# 3. Create virtualenv
python3.11 -m venv .venv
source .venv/bin/activate

# 4. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Copy and configure environment
cp .env.example .env
# Edit GPU settings in .env:
#   ACCEL_MODE=cuda
#   N_GPU_LAYERS=99
#   CTX_SIZE=8192
#   N_THREADS=8
#   FLASH_ATTN=true
#   CACHE_TYPE_K=q8_0
#   CACHE_TYPE_V=q8_0

# 6. Create data directory
mkdir -p data

# 7. Download GLM-OCR model (Q8_0 — 6.5 GB, better quality on GPU)
mkdir -p models
pip install huggingface-hub
huggingface-cli download ggml-org/GLM-OCR-GGUF Q8_0.gguf --local-dir ./models

# 8. Download and build llama.cpp with CUDA support
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="60;61;70;75;80;86;89;90"
cmake --build build --config Release -j$(nproc)
cd ..

# 9. Start llama.cpp server with GPU offload
./llama.cpp/build/bin/llama-server \
  -m ./models/Q8_0.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  --n-gpu-layers 99 \
  --flash-attn \
  --ctx-size 8192 \
  --batch-size 512 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0

# 10. In another terminal, start lightning-ocr
source .venv/bin/activate
ACCEL_MODE=cuda N_GPU_LAYERS=99 CTX_SIZE=8192 FLASH_ATTN=true \
  uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --loop uvloop
```

---

## NVIDIA GPU Deep Dive

### VRAM Requirements by GPU

| GPU | VRAM | Max Quant | Model RAM | Layers | Context | Tok/s |
|-----|------|-----------|-----------|--------|---------|-------|
| GTX 1050 Ti | 4 GB | Q4_K_M | ~3.8 GB | 16 | 1024 | 10–15 |
| GTX 1060 | 6 GB | Q4_K_M | ~3.8 GB | 20 | 2048 | 20–30 |
| RTX 2060 | 6 GB | Q4_K_M | ~3.8 GB | 24 | 2048 | 30–40 |
| RTX 2070 | 8 GB | Q4_K_M | ~3.8 GB | 32 | 4096 | 40–50 |
| RTX 3060 | 12 GB | Q8_0 | ~6.5 GB | 99 | 4096 | 60–80 |
| RTX 3080 | 10 GB | Q8_0 | ~6.5 GB | 99 | 8192 | 80–100 |
| RTX 4070 | 12 GB | Q8_0 | ~6.5 GB | 99 | 8192 | 90–120 |
| RTX 3090 | 24 GB | F16 | ~13 GB | 99 | 16384 | 100–140 |
| RTX 4090 | 24 GB | F16 | ~13 GB | 99 | 16384 | 150–200 |
| RTX 5090 | 32 GB | F16 | ~13 GB | 99 | 32768 | 200–300 |

### GPU Settings Cheat Sheet

```bash
# ── 4 GB VRAM (GTX 1050 Ti) ──
# Use Q4_K_M model, limited context
N_GPU_LAYERS=16 CTX_SIZE=1024 CACHE_TYPE_K=q8_0 CACHE_TYPE_V=q8_0

# ── 6 GB VRAM (GTX 1060, RTX 2060) ──
# Use Q4_K_M model, moderate context
N_GPU_LAYERS=24 CTX_SIZE=2048 FLASH_ATTN=true

# ── 8-10 GB VRAM (RTX 2070, 3080) ──
# Use Q8_0 or Q4_K_M, large context
N_GPU_LAYERS=99 CTX_SIZE=8192 FLASH_ATTN=true CACHE_TYPE_K=q8_0

# ── 12 GB VRAM (RTX 3060, 4070) ──
# Q8_0 model fits comfortably, full offload
N_GPU_LAYERS=99 CTX_SIZE=8192 FLASH_ATTN=true

# ── 16-24 GB VRAM (RTX 4080, 3090, 4090) ──
# F16 quality, maximum context
N_GPU_LAYERS=99 CTX_SIZE=16384 FLASH_ATTN=true CACHE_TYPE_K=f16

# ── 32 GB+ VRAM (RTX 5090, A6000) ──
# F16 with maximum context
N_GPU_LAYERS=99 CTX_SIZE=32768 FLASH_ATTN=true CACHE_TYPE_K=f16
```

### Troubleshooting GPU Issues

#### Problem: "CUDA error: out of memory"

**Cause:** Model + context + KV cache doesn't fit in VRAM.

**Solutions (in order of effectiveness):**

```bash
# 1. Reduce GPU layers (most effective)
N_GPU_LAYERS=20  # Try 20 first, adjust down

# 2. Reduce context size
CTX_SIZE=1024  # Smaller context = less KV cache

# 3. Use heavier KV cache quantisation
CACHE_TYPE_K=q4_0  # q8_0 → q4_0 saves ~50% KV cache RAM
CACHE_TYPE_V=q4_0

# 4. Use smaller model quant
# Switch from Q8_0 to Q4_K_M (uses ~40% less RAM)
# In docker-compose.gpu.yml, change -hf ggml-org/GLM-OCR-GGUF:Q8_0 to :Q4_K_M

# 5. Disable flash attention
FLASH_ATTN=false  # Uses less VRAM per layer
```

#### Problem: "ggml_cuda_init: found 0 CUDA devices"

**Cause:** Docker can't see the GPU, or NVIDIA drivers aren't installed.

```bash
# 1. Check NVIDIA drivers
nvidia-smi

# 2. Check nvidia-container-toolkit
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi

# 3. Install if missing
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

#### Problem: "Failed to load model: file not found"

**Cause:** llama.cpp auto-download failed or model path is wrong.

```bash
# 1. Manually download the model
pip install huggingface-hub
huggingface-cli download ggml-org/GLM-OCR-GGUF Q4_K_M.gguf --local-dir ./models

# 2. Mount it in Docker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm \
  -v $(pwd)/models:/models llama \
  ./llama-server -m /models/Q4_K_M.gguf --host 0.0.0.0 --port 8080 --n-gpu-layers 99
```

### Compiling llama.cpp for CUDA

If the pre-built Docker image doesn't work for your GPU architecture:

```bash
# 1. Check your GPU's compute capability
nvidia-smi --query-gpu=name,compute_cap --format=csv

# Common architectures:
#   GTX 1060:      6.1  (CMAKE_CUDA_ARCHITECTURES="61")
#   RTX 2060:      7.5  (CMAKE_CUDA_ARCHITECTURES="75")
#   RTX 3060/70:  8.6   (CMAKE_CUDA_ARCHITECTURES="86")
#   RTX 3080/90:  8.6   (CMAKE_CUDA_ARCHITECTURES="86")
#   RTX 4070:     8.9   (CMAKE_CUDA_ARCHITECTURES="89")
#   RTX 4090:     8.9   (CMAKE_CUDA_ARCHITECTURES="89")
#   RTX 5090:    10.0   (CMAKE_CUDA_ARCHITECTURES="90")

# 2. Build with your architecture
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES="86;89;90"  # Adjust for your GPU
cmake --build build --config Release -j$(nproc)

# 3. Or build for all common architectures
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="60;61;70;75;80;86;89;90"
cmake --build build --config Release -j$(nproc)
```

---

## Post-Install Verification

### 1. Check all services are running

```bash
# Docker
docker ps
# Expected: lightning-ocr-app (healthy), lightning-ocr-llama-cpu (running)

# Manual
curl http://localhost:8000/api/health
```

### 2. Verify GPU acceleration (NVIDIA)

```bash
# Docker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml logs llama | grep -i "cuda"
# Expected: "ggml_cuda_init: found 1 CUDA devices: Device 0: NVIDIA GeForce RTX ..."

# Manual
./llama.cpp/build/bin/llama-server ... 2>&1 | grep -i "cuda"
```

### 3. Run an OCR test

```bash
# Create a test image
python3 -c "
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (400, 200), 'white')
d = ImageDraw.Draw(img)
d.text((20, 50), 'Hello, lightning-ocr!', fill='black')
d.text((20, 100), 'GPU Acceleration Test', fill='blue')
img.save('test.png')
"

# OCR it
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=document" \
  -F "file=@test.png"
# Expected: "Hello, lightning-ocr!  GPU Acceleration Test"
```

### 4. Check GPU utilization

```bash
nvidia-smi -l 1
# Watch for GPU-Util and Memory-Usage while OCR runs
```

### 5. Run the test suite

```bash
cd tests
pip install -r requirements.txt
python test_client.py --url http://localhost:8000
```

---

## Configuration

### Security Configuration

```bash
# .env — Production security settings
API_KEY="your-strong-secret-here"  # Required for authentication
# Test it:
curl -H "Authorization: Bearer your-strong-secret-here" http://localhost:8000/api/health
```

### Environment Profiles

#### Low-end CPU (4 GB RAM, no GPU)

```env
ACCEL_MODE=cpu
N_GPU_LAYERS=0
CTX_SIZE=1024
N_THREADS=4
FLASH_ATTN=false
MLOCK=false
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
TESSERACT_ENABLED=true
EASYOCR_ENABLED=false
```

#### Mid-range CPU (8-16 GB RAM, no GPU)

```env
ACCEL_MODE=cpu
N_GPU_LAYERS=0
CTX_SIZE=4096
N_THREADS=8
FLASH_ATTN=false
MLOCK=true
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
```

#### NVIDIA GPU / 8 GB VRAM (GTX 1080, RTX 2080)

```env
ACCEL_MODE=cuda
N_GPU_LAYERS=24
CTX_SIZE=4096
FLASH_ATTN=true
MLOCK=false
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
```

#### NVIDIA GPU / 12-16 GB VRAM (RTX 3080, 4070)

```env
ACCEL_MODE=cuda
N_GPU_LAYERS=99
CTX_SIZE=8192
FLASH_ATTN=true
MLOCK=false
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
```

#### NVIDIA GPU / 24 GB VRAM (RTX 3090, 4090, 5090)

```env
ACCEL_MODE=cuda
N_GPU_LAYERS=99
CTX_SIZE=16384
FLASH_ATTN=true
MLOCK=false
CACHE_TYPE_K=f16
CACHE_TYPE_V=f16
```

#### Intel Arc / UHD 730+ (SYCL/Vulkan)

```env
ACCEL_MODE=sycl
N_GPU_LAYERS=auto
CTX_SIZE=4096
FLASH_ATTN=true
MLOCK=false
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
```

#### Apple Silicon (M1/M2/M3/M4/M5)

```env
ACCEL_MODE=auto
N_GPU_LAYERS=99
CTX_SIZE=8192
FLASH_ATTN=true
MLOCK=false
CACHE_TYPE_K=q8_0
CACHE_TYPE_V=q8_0
```