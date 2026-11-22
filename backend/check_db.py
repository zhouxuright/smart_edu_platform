
import asyncio
import os
from sqlalchemy import text
from edu_app.core.database import AsyncSessionLocal

# Set environment variable for OpenMP fix just in case, though not strictly needed for DB check
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

async def check_data():
    async with AsyncSessionLocal() as db:
        # Check Documents
        result = await db.execute(text("SELECT count(*) FROM documents"))
        doc_count = result.scalar()
        print(f"Total Documents: {doc_count}")

        if doc_count > 0:
            result = await db.execute(text("SELECT id, title, length(content), owner_id, tenant_id FROM documents"))
            docs = result.fetchall()
            for doc in docs:
                print(f"Doc ID: {doc[0]}, Title: {doc[1]}, Len: {doc[2]}, Owner: {doc[3]}, Tenant: {doc[4]}")

        # Check Embeddings
        result = await db.execute(text("SELECT count(*) FROM document_embeddings"))
        emb_count = result.scalar()
        print(f"Total Embeddings: {emb_count}")

        if emb_count > 0:
            result = await db.execute(text("SELECT id, document_id FROM document_embeddings LIMIT 5"))
            embs = result.fetchall()
            for emb in embs:
                print(f"Emb ID: {emb[0]}, Doc ID: {emb[1]}")

if __name__ == "__main__":
    asyncio.run(check_data())
