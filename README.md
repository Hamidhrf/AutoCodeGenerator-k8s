# AutoCodeGenerator — Kubernetes Edition

> **This is a Kubernetes-compatible fork of [AutoCodeGenerator](https://github.com/BhanuPDas/AutoCodeGenerator) by BhanuPDas.**  
> The original project is a full-stack AI code generation platform powered by CodeLlama-13B. This fork adds complete Kubernetes deployment support with GPU scheduling, service discovery, and persistent storage.

---

## What I Added

The original project was designed to run with Docker Compose on a single machine with hardcoded IPs. I refactored it for production-grade Kubernetes deployment:

- **Kubernetes manifests** for all 5 services (inference, Java backend, React UI, Redis, PostgreSQL)
- **Fixed hardcoded IPs** throughout the source code, replacing them with Kubernetes service DNS names
- **NVIDIA GPU scheduling** via RuntimeClass and device plugin configuration for CRI-O runtime
- **HuggingFace secret management** using Kubernetes Secrets (never stored in code or YAML)
- **Persistent storage** for PostgreSQL using hostPath volumes
- **nginx reverse proxy** configuration for the React frontend to route `/api/` calls to the Java backend
- **Tested on NVIDIA H100 NVL** (95GB VRAM) achieving 38.7 tokens/sec with CodeLlama-13B

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Kubernetes Cluster                 │
│  Namespace: autocodegens                        │
│                                                 │
│  ┌──────────┐    ┌──────────────┐               │
│  │  React   │───▶│ Java Spring  │               │
│  │  UI      │    │ Boot Backend │               │
│  │ :30090   │    │ :4010        │               │
│  └──────────┘    └──────┬───────┘               │
│                         │                       │
│              ┌──────────┴──────────┐            │
│              │                     │            │
│      ┌───────▼──────┐   ┌─────────▼──────┐      │
│      │  CodeLlama   │   │   PostgreSQL   │      │
│      │  13B FastAPI │   │   + Redis      │      │
│      │  :5050 (GPU) │   │                │      │
│      └──────────────┘   └────────────────┘      │
└─────────────────────────────────────────────────┘
```

| Service | Image | Port | Description |
|---|---|---|---|
| ui | hamidhrf/autocodegens-ui | 30090 (NodePort) | React frontend + nginx proxy |
| code-generator | hamidhrf/autocodegens-java | 4010 | Java Spring Boot API |
| inference | hamidhrf/autocodegens-inference | 5050 | CodeLlama-13B FastAPI (GPU) |
| redis | redis:7-alpine | 6379 | Session/cache store |
| postgres | postgres:16-alpine | 5432 | Persistent storage |

---

## Prerequisites

- Kubernetes cluster (tested on v1.32 with CRI-O runtime)
- NVIDIA GPU with 27GB+ VRAM (tested on H100 NVL 95GB)
- NVIDIA device plugin configured in Kubernetes
- Docker Hub account
- HuggingFace account with access to [meta-llama/CodeLlama-13b-Instruct-hf](https://huggingface.co/meta-llama/CodeLlama-13b-Instruct-hf)

---

## Quick Start

### 1. Clone this repo

```bash
git clone https://github.com/hamidhrf/AutoCodeGenerator-k8s
cd AutoCodeGenerator-k8s
```

### 2. Build and push images

```bash
docker login -u hamidhrf

# Inference service (needs HuggingFace token)
cd inference_pipeline
docker build --build-arg HF_TOKEN=<your-hf-token> -t hamidhrf/autocodegens-inference:latest .
docker push hamidhrf/autocodegens-inference:latest

# Java backend
cd ../code-generator
docker build -t hamidhrf/autocodegens-java:latest .
docker push hamidhrf/autocodegens-java:latest

# React UI
cd ../UI/code-generator
docker build -t hamidhrf/autocodegens-ui:latest .
docker push hamidhrf/autocodegens-ui:latest
```

### 3. Deploy to Kubernetes

```bash
# Create namespace and HuggingFace secret
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic hf-secret \
  --from-literal=token=<your-hf-token> \
  -n autocodegens

# Deploy dependencies first
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/redis/

# Deploy application services
kubectl apply -f k8s/inference/
kubectl apply -f k8s/java-service/
kubectl apply -f k8s/ui/
```

### 4. Verify

```bash
kubectl get pods -n autocodegens
# All 5 pods should be Running
# Note: inference pod takes 5-10 minutes on first start (model download)
```

### 5. Access

Open your browser at `http://<your-node-ip>:30090`

---

## Source Code Changes

The following files were modified from the original to work with Kubernetes:

| File | Change |
|---|---|
| `UI/code-generator/src/Content.tsx` | Replaced hardcoded IP with relative `/api/` path |
| `UI/code-generator/src/App.tsx` | Replaced hardcoded Grafana IP |
| `UI/code-generator/nginx.conf` | Added `/api/` proxy pass to Java backend service |
| `UI/code-generator/Dockerfile` | Updated to use custom nginx.conf |
| `code-generator/src/main/resources/application.properties` | Replaced all IPs with K8s service DNS names |
| `inference_pipeline/inference_13b_batch_c5.py` | Replaced hardcoded Redis IP with service name |
| `inference_pipeline/inference_13b_gptq_c4.py` | Replaced hardcoded Redis IP with service name |

---

## Kubernetes Manifests

```
k8s/
├── namespace.yaml
├── inference/
│   ├── deployment.yaml       # GPU pod with runtimeClassName: nvidia
│   └── service.yaml          # ClusterIP on port 5050
├── java-service/
│   ├── deployment.yaml
│   └── service.yaml          # ClusterIP on port 4010
├── ui/
│   ├── deployment.yaml
│   └── service.yaml          # NodePort 30090
├── redis/
│   ├── deployment.yaml
│   └── service.yaml
└── postgres/
    ├── deployment.yaml       # hostPath volume for persistence
    └── service.yaml
```

---

## Performance (NVIDIA H100 NVL)

| Metric | Value |
|---|---|
| Model | CodeLlama-13B-Instruct |
| Inference time | ~2.6 seconds |
| Token throughput | 38.7 tokens/sec |
| GPU utilization | 46.7% |
| VRAM used | 27.0 GB / 93.6 GB |

---

## Deployment Guide

For a full step-by-step guide including NVIDIA driver setup, CRI-O configuration, and troubleshooting, see [DEPLOYMENT.md](./DEPLOYMENT.md).

---

## Original Project

This project is based on [AutoCodeGenerator](https://github.com/BhanuPDas/AutoCodeGenerator) by BhanuPDas. All credit for the original application architecture, AI pipeline, and frontend design goes to the original authors.

---

## License

See the original repository for license information.
