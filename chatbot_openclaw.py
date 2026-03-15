#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Chatbot trợ lý toàn năng từ mã nguồn OpenClaw
Chạy ngầm trên Windows 11, offline, không API key
"""

import os
import sys
import shutil
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

import git
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings
import requests
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# ---------------------------- CẤU HÌNH ----------------------------
REPO_URL = "https://github.com/openclaw/openclaw.git"
# Sử dụng đường dẫn tuyệt đối hoặc tương đối phù hợp với Windows
BASE_DIR = Path(__file__).parent.absolute()
REPO_DIR = BASE_DIR / "openclaw_repo"
CHROMA_DIR = BASE_DIR / "chroma_db"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
LLM_MODEL = "llama3.2:1b"  # Đảm bảo đã pull model này qua ollama
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K_RESULTS = 5

# Cấu hình logging (ghi cả ra file để theo dõi khi chạy ngầm)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / "chatbot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------- CÁC HÀM XỬ LÝ ----------------------------

def clone_repo(repo_url: str, target_dir: Path) -> None:
    """Clone repository nếu chưa tồn tại."""
    if target_dir.exists():
        logger.info(f"Thư mục {target_dir} đã tồn tại, bỏ qua clone.")
        return
    logger.info(f"Đang clone {repo_url} vào {target_dir}...")
    git.Repo.clone_from(repo_url, str(target_dir))
    logger.info("Clone hoàn tất.")

def is_text_file(filepath: Path) -> bool:
    """Kiểm tra file có phải là văn bản dựa trên phần mở rộng."""
    text_extensions = {'.py', '.js', '.cpp', '.c', '.h', '.hpp', '.md', '.rst', '.txt', 
                       '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', 
                       '.sh', '.bash', '.html', '.css', '.xml', '.sql', 
                       '.java', '.go', '.rs', '.php', '.rb', '.pl', '.lua',
                       '.cmake', '.mk', '.ps1', '.bat', '.cmd'}
    return filepath.suffix.lower() in text_extensions

def load_documents(repo_path: Path) -> List[Dict[str, str]]:
    """Đọc tất cả file văn bản trong repo."""
    documents = []
    for filepath in repo_path.rglob('*'):
        if filepath.is_file() and is_text_file(filepath):
            # Bỏ qua các thư mục không mong muốn
            if any(part.startswith('.') or part in {'__pycache__', 'build', 'dist', 'venv', 'env'} 
                   for part in filepath.parts):
                continue
            try:
                # Thử đọc với utf-8, nếu lỗi thì dùng latin1
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(filepath, 'r', encoding='latin1') as f:
                        content = f.read()
                relative_path = filepath.relative_to(repo_path)
                doc_id = hashlib.md5(f"{relative_path}:{content[:100]}".encode()).hexdigest()
                documents.append({
                    "id": doc_id,
                    "content": content,
                    "metadata": {
                        "source": str(relative_path),
                        "file": filepath.name,
                        "path": str(filepath)
                    }
                })
                logger.debug(f"Đã đọc: {relative_path}")
            except Exception as e:
                logger.warning(f"Lỗi đọc file {filepath}: {e}")
    logger.info(f"Tổng số file văn bản đã đọc: {len(documents)}")
    return documents

def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Chia văn bản thành các chunk."""
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            # Tìm dấu xuống dòng để cắt
            for i in range(end, max(start, end - overlap), -1):
                if text[i] == '\n':
                    end = i + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < text_len else text_len
    return chunks

def split_documents(documents: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Chia document thành các chunk nhỏ."""
    chunked_docs = []
    for doc in documents:
        chunks = split_text(doc["content"], CHUNK_SIZE, CHUNK_OVERLAP)
        for i, chunk in enumerate(chunks):
            chunked_docs.append({
                "id": f"{doc['id']}_{i}",
                "content": chunk,
                "metadata": {
                    **doc["metadata"],
                    "chunk_index": i
                }
            })
    logger.info(f"Tổng số chunk: {len(chunked_docs)}")
    return chunked_docs

def get_embedding_model(model_name: str):
    """Tải model embedding local."""
    logger.info(f"Đang tải model embedding: {model_name}")
    return SentenceTransformer(model_name)

def create_vector_store(chunks: List[Dict[str, Any]], embed_model, persist_dir: Path):
    """Tạo ChromaDB collection và thêm dữ liệu."""
    client = chromadb.PersistentClient(path=str(persist_dir), settings=Settings(anonymized_telemetry=False))
    try:
        client.delete_collection("openclaw")
    except:
        pass
    collection = client.create_collection(name="openclaw")
    
    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["content"] for chunk in chunks]
    metadatas = [chunk["metadata"] for chunk in chunks]
    
    batch_size = 100
    for i in range(0, len(documents), batch_size):
        batch_docs = documents[i:i+batch_size]
        batch_ids = ids[i:i+batch_size]
        batch_metas = metadatas[i:i+batch_size]
        embeddings = embed_model.encode(batch_docs).tolist()
        collection.add(
            documents=batch_docs,
            embeddings=embeddings,
            metadatas=batch_metas,
            ids=batch_ids
        )
        logger.info(f"Đã thêm batch {i//batch_size + 1}/{(len(documents)-1)//batch_size + 1}")
    
    logger.info(f"Đã lưu {collection.count()} vectors.")
    return collection

def query_similar(question: str, collection, embed_model, k: int = TOP_K_RESULTS) -> List[str]:
    """Tìm k đoạn liên quan nhất."""
    q_embedding = embed_model.encode([question]).tolist()
    results = collection.query(query_embeddings=q_embedding, n_results=k)
    return results['documents'][0] if results['documents'] else []

def generate_answer(question: str, context: List[str]) -> str:
    """Gọi Ollama để sinh câu trả lời."""
    if not context:
        return "Không tìm thấy thông tin liên quan trong mã nguồn."
    
    context_text = "\n\n---\n\n".join(context)
    prompt = f"""Bạn là trợ lý AI chuyên về dự án OpenClaw. Hãy trả lời câu hỏi dựa CHỈ trên các đoạn mã/tài liệu dưới đây. Nếu không có thông tin, hãy nói "Không tìm thấy trong mã nguồn." Đừng thêm thông tin ngoài.

Ngữ cảnh:
{context_text}

Câu hỏi: {question}

Trả lời (chi tiết, có thể trích dẫn file nếu có):"""

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 512
        }
    }
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "Lỗi: Không nhận được phản hồi.")
    except requests.exceptions.ConnectionError:
        logger.error("Không thể kết nối đến Ollama. Đảm bảo Ollama đang chạy (kiểm tra system tray hoặc chạy 'ollama serve').")
        return "Lỗi kết nối đến Ollama. Vui lòng khởi động Ollama."
    except Exception as e:
        logger.error(f"Lỗi khi gọi Ollama: {e}")
        return f"Lỗi xử lý: {str(e)}"

# ---------------------------- FASTAPI APP ----------------------------
app = FastAPI(title="OpenClaw Chatbot", description="Trợ lý offline từ mã nguồn", version="1.0")

# Global variables
collection = None
embed_model = None

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    answer: str
    sources: Optional[List[str]] = None

@app.post("/ask", response_model=QueryResponse)
async def ask(request: QueryRequest):
    if collection is None or embed_model is None:
        raise HTTPException(status_code=503, detail="Hệ thống chưa khởi tạo xong.")
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Câu hỏi không được để trống.")
    similar_chunks = query_similar(question, collection, embed_model)
    answer = generate_answer(question, similar_chunks)
    return QueryResponse(answer=answer, sources=similar_chunks[:3])

@app.get("/health")
async def health():
    return {"status": "ok"}

def start_api(host="0.0.0.0", port=8000):
    """Chạy FastAPI server (cho phép truy cập từ local network)."""
    uvicorn.run(app, host=host, port=port, log_level="info")

# ---------------------------- CLI MODE ----------------------------
def cli_mode():
    logger.info("Chế độ CLI. Gõ 'exit' để thoát.")
    while True:
        question = input("\nBạn: ").strip()
        if question.lower() in ('exit', 'quit'):
            break
        if not question:
            continue
        similar_chunks = query_similar(question, collection, embed_model)
        answer = generate_answer(question, similar_chunks)
        print(f"\nBot: {answer}")

# ---------------------------- KHỞI TẠO ----------------------------
def initialize():
    logger.info("=== BẮT ĐẦU KHỞI TẠO HỆ THỐNG ===")
    clone_repo(REPO_URL, REPO_DIR)
    docs = load_documents(REPO_DIR)
    if not docs:
        logger.error("Không tìm thấy file văn bản nào.")
        sys.exit(1)
    chunks = split_documents(docs)
    embed_model = get_embedding_model(EMBED_MODEL_NAME)
    collection = create_vector_store(chunks, embed_model, CHROMA_DIR)
    logger.info("=== KHỞI TẠO HOÀN TẤT ===")
    return collection, embed_model

# ---------------------------- MAIN ----------------------------
if __name__ == "__main__":
    # Kiểm tra nếu chưa có vector DB thì khởi tạo
    if not CHROMA_DIR.exists():
        collection, embed_model = initialize()
    else:
        logger.info("Đã tìm thấy vector DB cũ, đang tải...")
        client = chromadb.PersistentClient(path=str(CHROMA_DIR), settings=Settings(anonymized_telemetry=False))
        collection = client.get_collection("openclaw")
        embed_model = get_embedding_model(EMBED_MODEL_NAME)
        logger.info(f"Đã tải {collection.count()} vectors.")
    
    # Xử lý tham số dòng lệnh
    if len(sys.argv) > 1 and sys.argv[1] == "--api":
        # Chạy API server (có thể chạy ngầm)
        logger.info("Khởi động API server tại http://localhost:8000")
        start_api()
    else:
        cli_mode()