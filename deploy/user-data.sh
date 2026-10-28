#!/bin/bash
# EC2 user data (runs once as root at first boot): fetch the deploy bundle from S3 with the instance role, then bootstrap.
set -euo pipefail
exec > >(tee /var/log/careerpilot-userdata.log) 2>&1
BUCKET=careerpilot-217856377501-aps1
export AWS_REGION=ap-south-1
mkdir -p /opt/careerpilot && cd /opt/careerpilot
aws s3 cp "s3://$BUCKET/deploy/bundle.tgz" /tmp/bundle.tgz --region "$AWS_REGION"
tar xzf /tmp/bundle.tgz
bash deploy/ec2-bootstrap.sh
