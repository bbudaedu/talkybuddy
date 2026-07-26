#!/bin/bash
# 說說學伴 — EC2 user-data 一鍵開機腳本（Amazon Linux 2023）
#
# 貼到「啟動執行個體 → 進階詳細資訊 → 使用者資料」欄位即可。
# 開機後 EC2 會自動：裝 Docker → 拉 repo → build image → 起容器。
#
# 前置條件（必須先做，否則容器起得來但打不到 Bedrock）：
#   1. 該 EC2 已附掛帶 deploy/aws/bedrock-policy.json 權限的 IAM Instance Profile
#   2. Bedrock console 已在 BEDROCK_REGION 開通 Anthropic 模型存取
#
# 進度查看：ssh 進去後 `sudo tail -f /var/log/talkybuddy-bootstrap.log`
set -euxo pipefail
exec > >(tee -a /var/log/talkybuddy-bootstrap.log) 2>&1

# ---- 需要你改的三個值 -------------------------------------------------
REPO_URL="https://github.com/YOUR_ACCOUNT/talkybuddy.git"   # ← 改成你的 repo
BEDROCK_REGION="us-west-2"                                   # ← 已開通模型的 region
BEDROCK_MODEL_ID=""      # ← 留空則用程式內建預設；建議填 preflight 查到的實際值
# ----------------------------------------------------------------------

echo "=== [1/5] 安裝 Docker + git ==="
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

echo "=== [2/5] 取得原始碼 ==="
install -d -o ec2-user -g ec2-user /opt/talkybuddy
sudo -u ec2-user git clone --depth 1 "$REPO_URL" /opt/talkybuddy/src
cd /opt/talkybuddy/src

echo "=== [3/5] 建立 image（含模型下載，約 5-10 分鐘）==="
docker build -f deploy/aws/Dockerfile -t talkybuddy:cloud .

echo "=== [4/5] 產生 JWT 密鑰 ==="
JWT_SECRET="$(openssl rand -hex 32)"
printf '%s' "$JWT_SECRET" > /opt/talkybuddy/jwt_secret
chmod 600 /opt/talkybuddy/jwt_secret

echo "=== [5/5] 啟動容器 ==="
# 注意：這裡刻意不傳任何 AWS 金鑰。容器內 boto3 會自動使用 EC2 的
# IAM Instance Profile（透過 IMDSv2 取得短期憑證）——金鑰不落地、會自動輪換。
docker run -d --name talkybuddy --restart unless-stopped \
  -p 8000:8000 \
  -e BEDROCK_REGION="$BEDROCK_REGION" \
  ${BEDROCK_MODEL_ID:+-e BEDROCK_MODEL_ID="$BEDROCK_MODEL_ID"} \
  -e TALKYBUDDY_CLOUD_PROVIDER=bedrock \
  -e TALKYBUDDY_PIPELINE_PROFILE=cloud \
  -e TALKYBUDDY_JWT_SECRET="$JWT_SECRET" \
  -e TALKYBUDDY_CONSENT_GRANTED=true \
  ${ELEVENLABS_API_KEY:+-e ELEVENLABS_API_KEY="$ELEVENLABS_API_KEY"} \
  -v talkybuddy-data:/app/data \
  talkybuddy:cloud

echo "=== 完成。健康檢查： ==="
sleep 15
curl -fsS http://localhost:8000/api/status && echo || echo "尚未就緒，稍候再試"
