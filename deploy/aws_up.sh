#!/usr/bin/env bash
# Create (once) the EC2 host for the NovaCRM agent: key pair, security group, t3.small (Amazon Linux 2023,
# Docker + Compose installed by user-data), Elastic IP. Safe to re-run: reuses resources tagged/named novacrm.
# Usage: AWS_PROFILE=jk-personal deploy/aws_up.sh
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-jk-personal}" AWS_REGION="${AWS_REGION:-ap-south-1}"
NAME=novacrm
KEY="$HOME/.ssh/$NAME-$AWS_REGION.pem"
TYPE="${INSTANCE_TYPE:-t3.small}"

if ! aws ec2 describe-key-pairs --key-names "$NAME" >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial --output text > "$KEY"
  chmod 600 "$KEY"; echo "key pair -> $KEY"
fi

VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME Name=vpc-id,Values="$VPC" \
     --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
  SG=$(aws ec2 create-security-group --group-name $NAME --description "NovaCRM agent" --vpc-id "$VPC" \
       --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]' \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]' >/dev/null
fi
# SSH only from this machine's current IP (re-run after your IP changes).
MYIP=$(curl -fsS https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" \
  >/dev/null 2>&1 || true

ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=$NAME \
     Name=instance-state-name,Values=pending,running,stopped --query 'Reservations[0].Instances[0].InstanceId' \
     --output text)
if [ "$ID" = "None" ]; then
  AMI=$(aws ec2 describe-images --owners amazon --filters 'Name=name,Values=al2023-ami-2023.*-x86_64' \
        'Name=state,Values=available' --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
  ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name $NAME \
       --security-group-ids "$SG" \
       --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3,Encrypted=true}' \
       --metadata-options HttpTokens=required \
       --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
       --user-data file://"$(dirname "$0")/user_data.sh" --query 'Instances[0].InstanceId' --output text)
  echo "launched $ID ($TYPE)"
fi
aws ec2 wait instance-running --instance-ids "$ID"

EIP=$(aws ec2 describe-addresses --filters Name=tag:Name,Values=$NAME --query 'Addresses[0].AllocationId' \
      --output text)
if [ "$EIP" = "None" ]; then
  EIP=$(aws ec2 allocate-address --domain vpc \
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
        --query AllocationId --output text)
fi
aws ec2 associate-address --instance-id "$ID" --allocation-id "$EIP" >/dev/null
IP=$(aws ec2 describe-addresses --allocation-ids "$EIP" --query 'Addresses[0].PublicIp' --output text)

echo "instance  $ID"
echo "ip        $IP"
echo "domain    ${IP//./-}.sslip.io"
echo "ssh       ssh -i $KEY ec2-user@$IP"
