# 說說學伴 — 雲端主線容器（cloud profile）
#
# 設計原則：容器內**絕不放 AWS 金鑰**。憑證一律走 boto3 標準鏈，
# 在 EC2 上由附掛的 IAM Instance Profile 自動提供 —— 這是「無腦搬遷」的核心：
# 同一個 image 在任何 AWS 帳號、任何 region 都能跑，只換環境變數。
#
# build（在 repo 根目錄執行）：
#   docker build -f deploy/aws/Dockerfile -t talkybuddy:cloud .
# run：
#   docker run -d --name talkybuddy -p 8000:8000 \
#     -e BEDROCK_REGION=us-west-2 \
#     -e BEDROCK_MODEL_ID=<以 python -m server.bedrock_converse 查到的值> \
#     -e TALKYBUDDY_CLOUD_PROVIDER=bedrock \
#     -e TALKYBUDDY_JWT_SECRET=<隨機值> \
#     -v talkybuddy-data:/app/data \
#     talkybuddy:cloud

FROM python:3.12-slim

# ffmpeg：pipeline 把瀏覽器 webm 轉 16k mono wav（有 RIFF fast path，但
# 瀏覽器來源一律需要它）。libsndfile1：soundfile 讀 wav 的系統相依。
# espeak-ng-data：sherpa-onnx 載 Piper voice 時要用它做音素化。少了這 19MB，
# TTSEngine.available() 仍回 True，但每次 synth() 都靜靜回 None——玩偶啞掉而且
# 沒有任何錯誤訊息。2026-08-01 決賽當天就是這樣才發現的（fetch_models.py 只抓
# SenseVoice 與 piper 的 .onnx，從來沒處理過 espeak 資料）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
        espeak-ng-data \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依賴先裝，讓 layer cache 在改程式碼時不失效
COPY deploy/aws/requirements-cloud.txt /app/deploy/aws/requirements-cloud.txt
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r /app/deploy/aws/requirements-cloud.txt

# 模型下載獨立一層（~580MB，最慢，最不常變）
COPY deploy/aws/fetch_models.py /app/deploy/aws/fetch_models.py
RUN pip install --no-cache-dir huggingface_hub \
    && python /app/deploy/aws/fetch_models.py --models-dir /app/models \
    && pip uninstall -y huggingface_hub \
    && rm -rf /root/.cache/huggingface

# config.ESPEAK_NG_DATA_DIR 寫死在 models/ 底下，所以把 apt 裝的那份接過來。
# 用 find 而不是寫死路徑：Debian 的位置隨架構不同（x86_64 在
# /usr/lib/x86_64-linux-gnu/，aarch64 在 /usr/lib/aarch64-linux-gnu/）。
# 找不到就讓 build 失敗——這比執行期靜默啞掉好得多。
RUN ESP="$(find /usr/lib /usr/share -maxdepth 3 -type d -name espeak-ng-data | head -1)" \
    && test -n "$ESP" \
    && ln -sfn "$ESP" /app/models/espeak-ng-data \
    && test -f /app/models/espeak-ng-data/phontab \
    && echo "espeak-ng-data -> $ESP"

# 應用程式碼放最後：改 code 只重建這層
COPY server/ /app/server/
COPY web/ /app/web/
COPY data/ /app/data/

# cloud profile：開機即 network_mode=cloud，雲端腦走 Bedrock
ENV TALKYBUDDY_PIPELINE_PROFILE=cloud \
    TALKYBUDDY_CLOUD_PROVIDER=bedrock \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/status || exit 1

CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
