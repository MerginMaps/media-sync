# AWS S3 setup

This guide shows how to prepare an AWS S3 bucket and credentials for media-sync with [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
You need permissions to manage S3 and IAM in your AWS account.

Replace `my-media-bucket` with a globally unique bucket name and `eu-west-1` with your region in all commands below.

## 1. Create a bucket

```shell
aws s3api create-bucket \
  --bucket my-media-bucket \
  --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1
```

## 2. Make files readable

Media-sync writes URLs of uploaded files to your project, e.g. `https://my-media-bucket.s3.eu-west-1.amazonaws.com/images/photo.jpg`.
For QGIS or a web browser to display them, files in the bucket need to be readable. The simplest option is to allow public read access to objects
in the bucket (anyone who knows the URL can download the file):

```shell
aws s3api put-public-access-block \
  --bucket my-media-bucket \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false

aws s3api put-bucket-policy --bucket my-media-bucket --policy '{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicRead",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::my-media-bucket/*"
  }]
}'
```

If you do not want a public bucket, you can serve the files e.g. through CloudFront and set `S3__PUBLIC_URL` to its URL.

## 3. Create credentials for media-sync

Media-sync needs only `s3:ListBucket` on the bucket (to check it exists) and `s3:PutObject` on its objects
(plus `s3:CreateBucket` if you want media-sync to create the bucket, which is not needed here).

Create an IAM user with access only to the bucket and its access key:

```shell
aws iam create-user --user-name mergin-media-sync

aws iam put-user-policy --user-name mergin-media-sync --policy-name media-sync-bucket --policy-document '{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::my-media-bucket"},
    {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::my-media-bucket/*"}
  ]
}'

aws iam create-access-key --user-name mergin-media-sync
```

Note `AccessKeyId` and `SecretAccessKey` from the output and use them as `S3__ACCESS_KEY` and `S3__SECRET_KEY`.

If you run media-sync on AWS (e.g. EC2 or ECS), you can attach the same policy to its IAM role instead and leave
`S3__ACCESS_KEY` and `S3__SECRET_KEY` empty - the [default AWS credentials chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html) is used then.

## 4. Configure media-sync

Use the bucket with these settings (the bucket already exists, so media-sync does not need to create it):

```shell
  -e DRIVER=s3 \
  -e S3__BUCKET=my-media-bucket \
  -e S3__REGION=eu-west-1 \
  -e S3__ACCESS_KEY=<AccessKeyId> \
  -e S3__SECRET_KEY=<SecretAccessKey> \
  -e S3__CREATE_BUCKET=false \
```

See [README](../README.md#using-s3-backend) for all S3 settings.
