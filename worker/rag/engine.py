"""RAG engine using ChromaDB + sentence-transformers."""
import os
from worker.config import config


class RAGEngine:
    def __init__(self):
        self.chroma = None
        self.collection = None
        self.embedder = None
        self._initialized = False

    async def _init(self):
        if self._initialized:
            return

        try:
            import chromadb
            self.chroma = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
            self.collection = self.chroma.get_or_create_collection(
                name="yubilab_rag",
                metadata={"hnsw:space": "cosine"}
            )

            from sentence_transformers import SentenceTransformer
            self.embedder = SentenceTransformer(config.EMBEDDING_MODEL)
            self._initialized = True
        except ImportError:
            print("[RAG] Dependencies not installed, RAG disabled")

    async def upload_document(self, file_path, user_id, workspace_id):
        await self._init()
        if not self._initialized:
            return {"error": "RAG not available"}

        chunks = self._chunk_file(file_path)
        if not chunks:
            return {"error": "Could not read file"}

        embeddings = self.embedder.encode(chunks).tolist()

        doc_id = os.path.basename(file_path)
        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]

        try:
            existing = self.collection.get(where={"file_name": doc_id})
            if existing['ids']:
                self.collection.delete(ids=existing['ids'])
        except Exception:
            pass

        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=[{
                "file_name": doc_id,
                "file_path": file_path,
                "user_id": str(user_id),
                "workspace_id": workspace_id,
                "chunk_index": i
            } for i in range(len(chunks))]
        )

        return {"indexed_chunks": len(chunks), "file": doc_id}

    async def query(self, query_text, workspace_id=None, top_k=5):
        await self._init()
        if not self._initialized:
            return []

        query_embedding = self.embedder.encode([query_text]).tolist()

        where_filter = {}
        if workspace_id:
            where_filter["workspace_id"] = workspace_id

        try:
            results = self.collection.query(
                query_embeddings=query_embedding,
                n_results=top_k,
                where=where_filter if where_filter else None
            )

            output = []
            for doc, meta, dist in zip(
                results['documents'][0],
                results['metadatas'][0],
                results['distances'][0]
            ):
                output.append({
                    "content": doc,
                    "source": meta.get("file_name", "unknown"),
                    "score": round(1 - dist, 3)
                })
            return output
        except Exception as e:
            print(f"[RAG] Query error: {e}")
            return []

    def _chunk_file(self, file_path, chunk_size=500, overlap=50):
        """Read file and split into chunks."""
        try:
            ext = os.path.splitext(file_path)[1].lower()

            if ext == '.pdf':
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                text = "\n".join(p.extract_text() or "" for p in reader.pages)
            elif ext == '.docx':
                from docx import Document
                doc = Document(file_path)
                text = "\n".join(p.text for p in doc.paragraphs)
            else:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    text = f.read()

            chunks = []
            words = text.split()
            for i in range(0, len(words), chunk_size - overlap):
                chunk = " ".join(words[i:i + chunk_size])
                if chunk.strip():
                    chunks.append(chunk)

            return chunks
        except Exception as e:
            print(f"[RAG] Chunk error for {file_path}: {e}")
            return []


rag_engine = RAGEngine()
