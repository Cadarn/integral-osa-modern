#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "typer>=0.12.0",
# ]
# ///
"""
Ephemeral EC2 Worker to run dal_dump / dal_list diagnostics directly inside
the native ARM64 Docker container against S3-staged CALDB data.
"""

import base64

import boto3

AMI_ARM64 = "ami-08bb9a392e39dc6e6"  # AL2023 ARM64
IMAGE = "cadarn/osa:11-native-arm64"
S3_BUCKET = "integral-cloud-analysis-data-537472396676"

USER_DATA = f"""#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/dal_diag.log | logger -t user-data -s 2>/dev/console) 2>&1

dnf update -y
dnf install -y --allowerasing docker awscli
systemctl enable --now docker

trap 'shutdown -h now' ERR EXIT

WORKDIR="/opt/diag"
mkdir -p "$WORKDIR/data"
cd "$WORKDIR"

echo "=== 1. Pulling container image ==="
docker pull {IMAGE}

echo "=== 2. Syncing minimal index and background files from S3 ==="
aws s3 sync "s3://{S3_BUCKET}/caldb/idx/ic/" "$WORKDIR/data/idx/ic/"
aws s3 sync "s3://{S3_BUCKET}/caldb/ic/ibis/bkg/" "$WORKDIR/data/ic/ibis/bkg/"
aws s3 sync "s3://{S3_BUCKET}/caldb/cat/hec/" "$WORKDIR/data/cat/hec/"

echo "=== 3. Running DAL diagnostics inside container ==="
docker run --rm \\
    -v "$WORKDIR/data:/data" \\
    {IMAGE} \\
    bash -c '
        set -x
        [ -f /init.sh ] && source /init.sh 2>/dev/null || true
        [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true
        export ISDC_ENV=/opt/osa
        export REP_BASE_PROD=/data
        export CURRENT_IC=/data

        echo "--- Testing dal_dump on ic_master_file.fits[1] ---"
        dal_dump "/data/idx/ic/ic_master_file.fits[1]" || true

        echo "--- Testing dal_dump on ic_master_file.fits[2] ---"
        dal_dump "/data/idx/ic/ic_master_file.fits[2]" || true

        echo "--- Testing dal_dump on ISGR-BACK-BKG-IDX.fits[1] ---"
        dal_dump "/data/idx/ic/ISGR-BACK-BKG-IDX.fits[1]" || true

        echo "--- Testing dal_list on ISGR-BACK-BKG-IDX.fits ---"
        dal_list "/data/idx/ic/ISGR-BACK-BKG-IDX.fits" || true

        echo "--- Checking permissions of /data ---"
        ls -la /data/idx/ic/ic_master_file.fits
        ls -la /data/idx/ic/ISGR-BACK-BKG-IDX.fits
        ls -la /data/ic/ibis/bkg/isgr_back_bkg_0007.fits || true
    '

echo "=== Diagnostics complete ==="
"""


def main():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    print("Launching ARM64 diagnostic instance...")
    resp = ec2.run_instances(
        ImageId=AMI_ARM64,
        InstanceType="c7g.large",
        MinCount=1,
        MaxCount=1,
        UserData=base64.b64encode(USER_DATA.encode("utf-8")).decode("utf-8"),
        InstanceInitiatedShutdownBehavior="terminate",
        SubnetId="subnet-a9e00af0",
        IamInstanceProfile={"Name": "IntegralCloudBenchmarkProfile"},
        InstanceMarketOptions={
            "MarketType": "spot",
            "SpotOptions": {
                "SpotInstanceType": "one-time",
                "InstanceInterruptionBehavior": "terminate",
            },
        },
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "DeleteOnTermination": True},
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "integral-dal-diagnostic"}],
            }
        ],
    )
    inst_id = resp["Instances"][0]["InstanceId"]
    print(f"Launched diagnostic instance: {inst_id}")


if __name__ == "__main__":
    main()
