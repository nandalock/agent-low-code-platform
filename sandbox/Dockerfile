# 沙箱镜像 —— 模型执行 bash / python 时 docker run 用的**执行环境**。
#
# 与 backend/Dockerfile、frontend/Dockerfile 职责不同：那两个是**应用镜像**
# （跑服务、带 CMD），这个是被 backend 经 docker.sock 临时拉起的执行环境。
# 因此：
#   - 不 COPY 项目代码（项目代码由工作区绑定挂载进来，见 docker.py 的 -v）
#   - 不要 CMD / ENTRYPOINT（镜像名之后的 argv 由调用方决定，见 docker.py）
#   - 依赖装进**镜像**，不是放宽 --read-only（只读根文件系统的边界不动）
#
# 改这里之后要重建：docker compose build sandbox-image
FROM python:3.12-slim

# 沙箱内工具。curl 是模型最常用的取数手段（HTTP 抓取 / 探活），git 用于拉
# 仓库；ca-certificates 是 curl https 的前提，缺了会报证书错误而不是网络错误，
# 很难从模型侧诊断。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# 沙箱内 Python 依赖：按实际用途预置，先给最小集，后续按需加。
# 刻意不装 torch / sentence-transformers / langchain —— 那是应用镜像的事，
# 塞进来会让本镜像变成几个 GB，而每次工具调用都要起一个容器。
RUN pip install --no-cache-dir \
    requests aiohttp pypdf numpy
