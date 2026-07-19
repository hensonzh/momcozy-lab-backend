from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "object-storage-integration-profile.md"


def test_object_storage_profile_documents_minio_and_metadata_boundary() -> None:
    text = DOC.read_text()

    for phrase in [
        "object-storage-integration",
        "check_object_storage_profile.py",
        "OBJECT_STORAGE_PROVIDER=minio",
        "OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000",
        "Local Docker Compose starts MinIO",
        "Postgres stores metadata",
    ]:
        assert phrase in text
