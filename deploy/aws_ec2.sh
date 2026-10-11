#!/usr/bin/env bash
# Deploy LabRat to a single EC2 instance (Docker + Caddy auto-HTTPS on <ip-dashes>.sslip.io).
# Usage: deploy/aws_ec2.sh            # create (or reuse) instance, print URL
#        REDEPLOY=1 deploy/aws_ec2.sh # terminate existing instance and redeploy current code
# Env: AWS_REGION (default us-west-2), INSTANCE_TYPE (t3.small), KEY_NAME (optional EC2 key pair for ssh),
#      ENV_FILE (default ./.env, uploaded privately and used as container --env-file).
# Code is shipped as a tarball to a private S3 bucket and fetched by the instance through a presigned URL
# (no instance IAM role needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-west-2}"
NAME="labrat-server"
SG_NAME="labrat-sg"
TYPE="${INSTANCE_TYPE:-t3.small}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="labrat-deploy-${ACCOUNT}-${REGION}"
AWS="aws --region $REGION"

echo "==> account $ACCOUNT region $REGION"

existing=$($AWS ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n "$existing" && "${REDEPLOY:-0}" != "1" ]]; then
  ip=$($AWS ec2 describe-addresses --filters "Name=tag:Name,Values=labrat-eip" --query 'Addresses[0].PublicIp' --output text 2>/dev/null)
  [[ -z "$ip" || "$ip" == "None" ]] && ip=$($AWS ec2 describe-instances --instance-ids $existing --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "==> instance already running: $existing"
  echo "URL: https://${ip//./-}.sslip.io   (phone: /phone)"
  exit 0
fi
if [[ -n "$existing" ]]; then
  echo "==> terminating $existing for redeploy"
  $AWS ec2 terminate-instances --instance-ids $existing >/dev/null
  $AWS ec2 wait instance-terminated --instance-ids $existing
fi

# --- stable public address (Elastic IP, reused across redeploys so the phone URL never changes) ---
EIP_ALLOC=$($AWS ec2 describe-addresses --filters "Name=tag:Name,Values=labrat-eip" --query 'Addresses[0].AllocationId' --output text 2>/dev/null)
if [[ -z "$EIP_ALLOC" || "$EIP_ALLOC" == "None" ]]; then
  EIP_ALLOC=$($AWS ec2 allocate-address --domain vpc --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=labrat-eip}]' --query AllocationId --output text)
fi
EIP=$($AWS ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp' --output text)
echo "==> elastic IP $EIP ($EIP_ALLOC)"

# --- bucket + code tarball ---
if ! $AWS s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [[ "$REGION" == "us-east-1" ]]; then $AWS s3api create-bucket --bucket "$BUCKET" >/dev/null
  else $AWS s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null; fi
  $AWS s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi
TMP="$(mktemp -d)"
mkdir -p "$ROOT/web" "$ROOT/phone"
tar -C "$ROOT" --exclude='.venv' --exclude='__pycache__' --exclude='node_modules' --exclude='.git' --exclude='.env' \
  -czf "$TMP/labrat.tgz" Dockerfile .dockerignore server web phone
$AWS s3 cp "$TMP/labrat.tgz" "s3://$BUCKET/labrat.tgz" >/dev/null
CODE_URL=$($AWS s3 presign "s3://$BUCKET/labrat.tgz" --expires-in 604800)
ENV_URL=""
if [[ -f "$ENV_FILE" ]]; then
  # docker --env-file keeps quotes and inline "# comments" literally: normalise to plain KEY=VALUE first
  CLEAN_ENV="$(mktemp)"
  python3 - "$ENV_FILE" "$CLEAN_ENV" <<'PY'
import re, sys
out = []
for line in open(sys.argv[1]):
    line = line.rstrip("\n")
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
    if not m:
        continue
    k, v = m.group(1), m.group(2).strip()
    if v[:1] in "\"'" and v[-1:] == v[:1] and len(v) >= 2:
        v = v[1:-1]
    else:
        v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        if v.startswith("#"):
            v = ""
    out.append(f"{k}={v}")
open(sys.argv[2], "w").write("\n".join(out) + "\n")
PY
  $AWS s3 cp "$CLEAN_ENV" "s3://$BUCKET/labrat.env" >/dev/null
  rm -f "$CLEAN_ENV"
  ENV_URL=$($AWS s3 presign "s3://$BUCKET/labrat.env" --expires-in 604800)
else
  echo "==> no $ENV_FILE; server will run with defaults (rule-based agent, in-memory store)"
fi

# --- security group ---
VPC=$($AWS ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$($AWS ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [[ -z "$SG" || "$SG" == "None" ]]; then
  SG=$($AWS ec2 create-security-group --group-name "$SG_NAME" --description "LabRat server" --vpc-id "$VPC" --query GroupId --output text)
  for p in 22 80 443; do
    $AWS ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port $p --cidr 0.0.0.0/0 >/dev/null
  done
fi
echo "==> security group $SG"

# --- AMI (Amazon Linux 2023) ---
AMI=$($AWS ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)

# --- user data ---
cat > "$TMP/user-data.sh" <<UD
#!/bin/bash
set -eux
dnf install -y docker
systemctl enable --now docker
mkdir -p /opt/labrat && cd /opt/labrat
curl -fsSL "$CODE_URL" -o labrat.tgz && tar xzf labrat.tgz
touch labrat.env
if [ -n "$ENV_URL" ]; then curl -fsSL "$ENV_URL" -o labrat.env; fi
docker build -t labrat .
docker rm -f labrat || true
docker run -d --name labrat --restart unless-stopped --env-file labrat.env -p 127.0.0.1:8000:8000 labrat
curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64" -o /usr/local/bin/caddy
chmod +x /usr/local/bin/caddy
TOKEN=\$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
IP=${EIP}
HOST="\$(echo \$IP | tr . -).sslip.io"
mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<CF
\$HOST {
  reverse_proxy 127.0.0.1:8000
}
CF
cat > /etc/systemd/system/caddy.service <<SV
[Unit]
Description=Caddy
After=network-online.target
[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
Restart=always
Environment=HOME=/root
[Install]
WantedBy=multi-user.target
SV
systemctl daemon-reload
systemctl enable --now caddy
UD

KEY_ARGS=()
[[ -n "${KEY_NAME:-}" ]] && KEY_ARGS=(--key-name "$KEY_NAME")
IID=$($AWS ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --security-group-ids "$SG" ${KEY_ARGS[@]+"${KEY_ARGS[@]}"} \
  --user-data "file://$TMP/user-data.sh" \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3}' \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
  --query 'Instances[0].InstanceId' --output text)
echo "==> launched $IID; waiting for it to run"
$AWS ec2 wait instance-running --instance-ids "$IID"
$AWS ec2 associate-address --instance-id "$IID" --allocation-id "$EIP_ALLOC" >/dev/null
IP="$EIP"
rm -rf "$TMP"
echo "==> instance $IID at $IP (docker build + TLS take ~3-5 min)"
echo "URL: https://${IP//./-}.sslip.io   (phone: /phone, health: /healthz)"
echo "Bridge: uv run python -m labrat_bridge --server wss://${IP//./-}.sslip.io --token \$LABRAT_TOKEN"
