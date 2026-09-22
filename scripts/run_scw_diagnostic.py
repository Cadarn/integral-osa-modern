#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "typer>=0.12.0",
# ]
# ///
"""
Targeted EC2 test worker to execute ibis_science_analysis on a single ScW (006000020010)
and stream full detailed stderr / stdout logs directly to S3.
"""

import base64
import gzip

import boto3

AMI_ARM64 = "ami-08bb9a392e39dc6e6"
IMAGE = "cadarn/osa:11-native-arm64"
S3_BUCKET = "integral-cloud-analysis-data-537472396676"

USER_DATA = f"""#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/scw_diag.log | logger -t user-data -s 2>/dev/console) 2>&1

dnf update -y
dnf install -y --allowerasing docker awscli
systemctl enable --now docker

trap 'aws s3 cp /var/log/scw_diag.log s3://{S3_BUCKET}/results/scw_diag_console.log || true; shutdown -h now' ERR EXIT

WORKDIR="/opt/integral_diag"
mkdir -p "$WORKDIR/data" "$WORKDIR/run"
cd "$WORKDIR"

echo "=== Pulling Docker image ==="
docker pull {IMAGE}

echo "=== Syncing CALDB, AUX, and single ScW from S3 ==="
aws s3 sync "s3://{S3_BUCKET}/caldb/" "$WORKDIR/data/" --exclude "*.log"
aws s3 sync "s3://{S3_BUCKET}/rev0060/data/aux/" "$WORKDIR/data/aux/"
aws s3 sync "s3://{S3_BUCKET}/rev0060/data/scw/0060/006000020010.001/" "$WORKDIR/data/scw/0060/006000020010.001/"

echo "006000020010.001" > "$WORKDIR/run/scw.list"

cat << 'EOF' > "$WORKDIR/run/run.sh"
#!/bin/bash
set -x
ulimit -s unlimited || true
[ -f /init.sh ] && source /init.sh 2>/dev/null || true
[ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true
export ISDC_ENV=/opt/osa
export REP_BASE_PROD=/data
export CFITSIO_INCLUDE_FILES=/opt/osa/templates
export ISDC_REF_CAT=/data/cat/hec/gnrl_refr_cat_0043.fits
export HOME=/home/integral
export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
mkdir -pv /home/integral/pfiles
export COMMONSCRIPT=1
export COMMONLOGFILE=+/home/integral/commonlog.txt
export DISPLAY=""

cd /home/integral
og_create idxSwg="scw.list" instrument="IBIS" ogid="og_bench" baseDir="./" obsDir="obs"
cd obs/og_bench
ibis_science_analysis \
    startLevel="COR" \
    endLevel="IMA2" \
    IBIS_II_ChanNum=1 \
    IBIS_II_E_band_min="18" \
    IBIS_II_E_band_max="60" \
    SWITCH_disableIsgri="no" \
    SWITCH_disablePICsIT="yes" \
    SWITCH_disableCompton="yes" \
    OBS1_CleanMode=1 \
    brPifThreshold=0.0 \
    CAT_refCat="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG>0]" \
    brSrcDOL="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG2==5&&ISGR_FLUX_1>100]" \
    IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
    IC_Alias="OSA"
EOF
chmod +x "$WORKDIR/run/run.sh"

echo "=== Executing pipeline inside container ==="
docker run --rm \
    --ulimit stack=-1:-1 \
    -v "$WORKDIR/run:/home/integral" \
    -v "$WORKDIR/data/scw:/data/scw" \
    -v "$WORKDIR/data/aux:/data/aux" \
    -v "$WORKDIR/data/ic:/data/ic" \
    -v "$WORKDIR/data/idx:/data/idx" \
    -v "$WORKDIR/data/cat:/data/cat" \
    -e HOME=/home/integral \
    {IMAGE} \
    bash /home/integral/run.sh || true

echo "=== Uploading commonlog.txt to S3 ==="
if [ -f "$WORKDIR/run/commonlog.txt" ]; then
    aws s3 cp "$WORKDIR/run/commonlog.txt" "s3://{S3_BUCKET}/results/scw_diag_commonlog.txt"
fi

echo "=== Finished test run ==="
"""


def main():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    print("Launching test worker...")
    compressed = gzip.compress(USER_DATA.encode("utf-8"))
    resp = ec2.run_instances(
        ImageId=AMI_ARM64,
        InstanceType="c7g.xlarge",
        MinCount=1,
        MaxCount=1,
        UserData=base64.b64encode(compressed).decode("ascii"),
        InstanceInitiatedShutdownBehavior="terminate",
        SubnetId="subnet-a9e00af0",
        IamInstanceProfile={"Name": "IntegralCloudBenchmarkProfile"},
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {"VolumeSize": 30, "VolumeType": "gp3", "DeleteOnTermination": True},
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "integral-scw-diagnostic"}],
            }
        ],
    )
    inst_id = resp["Instances"][0]["InstanceId"]
    print(f"Launched test instance: {inst_id}")


if __name__ == "__main__":
    main()
