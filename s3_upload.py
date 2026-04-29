# s3/s3_upload.py

import os
import uuid
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# ✅ Load environment variables
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")  # fallback
S3_BUCKET = os.getenv("S3_BUCKET")

# ✅ Use default AWS credential chain if possible (better for EC2 later)
s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION
)


def upload_file_to_s3(file_obj, filename: str, patient_id: str) -> dict:
    """
    Uploads a patient file to S3 dynamically.
    """

    if not S3_BUCKET:
        raise ValueError("S3_BUCKET is missing in environment variables.")

    if not patient_id:
        raise ValueError("patient_id is required.")

    if not filename:
        raise ValueError("filename is required.")

    # ✅ preserve extension
    file_extension = os.path.splitext(filename)[1]

    # ✅ avoid overwriting files
    unique_filename = f"{uuid.uuid4()}{file_extension}"

    s3_key = f"uploads/{patient_id}/{unique_filename}"

    try:
        s3_client.upload_fileobj(
            file_obj,
            S3_BUCKET,
            s3_key,
            ExtraArgs={
                "ServerSideEncryption": "AES256"  # simpler + safer fallback than KMS for now
            }
        )

        return {
            "bucket": S3_BUCKET,
            "s3_key": s3_key,
            "s3_path": f"s3://{S3_BUCKET}/{s3_key}",
            "original_filename": filename,
            "stored_filename": unique_filename,
            "patient_id": patient_id,
        }

    except ClientError as e:
        raise RuntimeError(f"S3 upload failed: {e}")


def download_file_from_s3(s3_key: str, local_path: str) -> str:
    """
    Downloads a file from S3 to local system (for parsing).
    """

    if not S3_BUCKET:
        raise ValueError("S3_BUCKET is missing.")

    if not s3_key:
        raise ValueError("s3_key is required.")

    try:
        s3_client.download_file(S3_BUCKET, s3_key, local_path)
        return local_path

    except ClientError as e:
        raise RuntimeError(f"S3 download failed: {e}")


def delete_file_from_s3(s3_key: str) -> bool:
    """
    Deletes a file from S3.
    """

    if not S3_BUCKET:
        raise ValueError("S3_BUCKET is missing.")

    if not s3_key:
        raise ValueError("s3_key is required.")

    try:
        s3_client.delete_object(
            Bucket=S3_BUCKET,
            Key=s3_key
        )
        return True

    except ClientError as e:
        raise RuntimeError(f"S3 delete failed: {e}")