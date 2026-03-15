#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script tự động cài đặt skill cho OpenClaw agent từ GitHub repository.
Hỗ trợ: clone repo, quét skill.md, hiển thị thông tin, cài đặt vào thư mục skills.
"""

import os
import sys
import shutil
import tempfile
import argparse
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Optional

try:
    import git
except ImportError:
    print("Thiếu thư viện gitpython. Cài đặt: pip install gitpython")
    sys.exit(1)

# --- Cấu hình mặc định ---
DEFAULT_REPO_URL = "https://github.com/allprogramming9999/master-skills.git"  # Thay bằng URL thực tế nếu biết
DEFAULT_OPENCLAW_SKILLS_DIR = os.path.expanduser("~/OpenClaw/skills")  # Đường dẫn mặc định đến thư mục skills của OpenClaw

# --- Hàm tiện ích ---
def print_header(text: str):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)

def print_success(text: str):
    print(f"✅ {text}")

def print_error(text: str):
    print(f"❌ {text}")

def print_info(text: str):
    print(f"ℹ️ {text}")

# --- Bước 1: Clone repository ---
def clone_repo(repo_url: str, target_dir: Path) -> Path:
    """Clone repository về thư mục tạm hoặc chỉ định."""
    if target_dir.exists():
        print_info(f"Thư mục {target_dir} đã tồn tại. Đang cập nhật bằng git pull...")
        try:
            repo = git.Repo(target_dir)
            origin = repo.remotes.origin
            origin.pull()
            print_success("Đã cập nhật repository.")
        except Exception as e:
            print_error(f"Không thể cập nhật: {e}. Bạn có muốn xóa và clone lại? (y/n): ", end="")
            if input().lower() == 'y':
                shutil.rmtree(target_dir)
                return clone_repo(repo_url, target_dir)
            else:
                sys.exit(1)
    else:
        print_info(f"Đang clone {repo_url} vào {target_dir}...")
        try:
            git.Repo.clone_from(repo_url, str(target_dir))
            print_success("Clone hoàn tất.")
        except Exception as e:
            print_error(f"Clone thất bại: {e}")
            sys.exit(1)
    return target_dir

# --- Bước 2: Tìm tất cả file skill (skill.md hoặc .md có cấu trúc) ---
def find_skill_files(repo_path: Path) -> List[Path]:
    """Tìm tất cả file có tên skill.md (hoặc có thể mở rộng)."""
    skill_files = []
    # Tìm file skill.md
    skill_files.extend(repo_path.rglob("skill.md"))
    # Nếu không có, tìm tất cả .md và giả định là skill (có thể lọc theo nội dung)
    if not skill_files:
        skill_files.extend(repo_path.rglob("*.md"))
    return skill_files

# --- Bước 3: Trích xuất thông tin từ file skill ---
def parse_skill_file(filepath: Path) -> Dict[str, str]:
    """Đọc nội dung file skill và trích xuất Context, Goals (nếu có)."""
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    # Tìm các section bằng regex đơn giản
    context_match = re.search(r'#+\s*Context\s*\n(.*?)(?=\n#+\s*|$)', content, re.DOTALL | re.IGNORECASE)
    goals_match = re.search(r'#+\s*Goals?\s*\n(.*?)(?=\n#+\s*|$)', content, re.DOTALL | re.IGNORECASE)
    rules_match = re.search(r'#+\s*Execution rules?\s*\n(.*?)(?=\n#+\s*|$)', content, re.DOTALL | re.IGNORECASE)
    
    info = {
        "file": str(filepath),
        "name": filepath.stem,
        "context": context_match.group(1).strip() if context_match else "N/A",
        "goals": goals_match.group(1).strip() if goals_match else "N/A",
        "rules": rules_match.group(1).strip() if rules_match else "N/A",
        "full_content": content
    }
    return info

def display_skill_info(skills_info: List[Dict[str, str]]):
    """Hiển thị danh sách skill với số thứ tự và thông tin ngắn."""
    print_header("DANH SÁCH SKILL TÌM THẤY")
    for idx, skill in enumerate(skills_info, 1):
        print(f"\n{idx}. {skill['name']}")
        print(f"   📄 File: {Path(skill['file']).relative_to(Path(skill['file']).parents[2])}")  # Hiển thị đường dẫn tương đối
        print(f"   🎯 Goals: {skill['goals'][:100]}..." if len(skill['goals']) > 100 else f"   🎯 Goals: {skill['goals']}")
        print(f"   📝 Context: {skill['context'][:100]}..." if len(skill['context']) > 100 else f"   📝 Context: {skill['context']}")
        print("-" * 40)

# --- Bước 4: Chọn skill để cài ---
def select_skills(skills_info: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Cho người dùng chọn skill bằng số thứ tự (có thể nhập nhiều, cách nhau dấu phẩy)."""
    print_info("Nhập số thứ tự các skill muốn cài (ví dụ: 1,3,5) hoặc 'all' để cài tất cả, 'q' để thoát.")
    while True:
        choice = input("Lựa chọn của bạn: ").strip().lower()
        if choice == 'q':
            return []
        if choice == 'all':
            return skills_info
        try:
            indices = [int(x.strip()) for x in choice.split(',') if x.strip().isdigit()]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(skills_info):
                    selected.append(skills_info[idx-1])
                else:
                    print_error(f"Số {idx} không hợp lệ. Bỏ qua.")
            if selected:
                return selected
            else:
                print_error("Không có skill nào được chọn. Thử lại.")
        except ValueError:
            print_error("Định dạng không hợp lệ. Vui lòng nhập số thứ tự cách nhau dấu phẩy.")

# --- Bước 5: Kiểm tra skill bằng AI khác (tùy chọn) ---
def check_skill_with_ai(skill_content: str, model: str = "claude") -> Optional[str]:
    """
    Gửi nội dung skill đến một AI (ví dụ: Claude) để kiểm tra.
    Cần cấu hình API key và endpoint.
    Hiện tại chỉ là giả lập, bạn có thể tích hợp thực tế nếu có API.
    """
    # Ví dụ giả lập: in ra nội dung và hỏi người dùng đánh giá
    print_info("Kiểm tra skill bằng AI... (tính năng này cần cấu hình API key)")
    print("Nội dung skill sẽ được gửi đến AI. Bạn có muốn tiếp tục? (y/n): ", end="")
    if input().lower() != 'y':
        return None
    # Ở đây có thể gọi API Claude/GPT, nhưng vì không có key nên ta giả định an toàn
    print_success("Skill được đánh giá là an toàn (giả lập).")
    return "OK"

# --- Bước 6: Cài đặt skill vào OpenClaw ---
def install_skills(selected_skills: List[Dict[str, str]], target_dir: Path, backup: bool = True):
    """Copy file skill vào thư mục skills của OpenClaw và ghi log."""
    if not target_dir.exists():
        print_info(f"Thư mục đích {target_dir} không tồn tại. Đang tạo...")
        target_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = target_dir / "install_log.txt"
    installed = []
    for skill in selected_skills:
        src = Path(skill['file'])
        dst = target_dir / src.name
        # Nếu file đã tồn tại, tạo bản sao lưu nếu cần
        if dst.exists() and backup:
            backup_name = dst.with_suffix(dst.suffix + ".backup")
            shutil.copy2(dst, backup_name)
            print_info(f"Đã tạo backup: {backup_name}")
        shutil.copy2(src, dst)
        installed.append(skill['name'])
        print_success(f"Đã cài skill '{skill['name']}' vào {dst}")
    
    # Ghi log
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        for skill in selected_skills:
            f.write(f"Installed: {skill['name']} from {skill['file']}\n")
    print_success(f"Đã cài {len(installed)} skill. Xem log tại {log_file}")

# --- Hàm chính ---
def main():
    parser = argparse.ArgumentParser(description="Cài đặt skill cho OpenClaw từ GitHub")
    parser.add_argument("--repo", default=DEFAULT_REPO_URL, help="URL GitHub repository chứa skill")
    parser.add_argument("--skills-dir", default=DEFAULT_OPENCLAW_SKILLS_DIR, help="Thư mục skills của OpenClaw")
    parser.add_argument("--temp", action="store_true", help="Clone vào thư mục tạm thay vì thư mục cố định")
    parser.add_argument("--check-ai", action="store_true", help="Kích hoạt kiểm tra skill bằng AI (cần cấu hình)")
    args = parser.parse_args()

    # Xác định thư mục clone
    if args.temp:
        clone_path = Path(tempfile.mkdtemp(prefix="openclaw_skills_"))
        print_info(f"Sử dụng thư mục tạm: {clone_path}")
    else:
        # Clone vào thư mục con trong thư mục hiện tại
        clone_path = Path.cwd() / "openclaw_skills_repo"
    
    # Bước 1: Clone
    repo_path = clone_repo(args.repo, clone_path)
    
    # Bước 2: Tìm skill files
    skill_files = find_skill_files(repo_path)
    if not skill_files:
        print_error("Không tìm thấy file skill nào (skill.md hoặc .md).")
        return
    
    # Bước 3: Parse thông tin
    skills_info = [parse_skill_file(f) for f in skill_files]
    display_skill_info(skills_info)
    
    # Bước 4: Chọn skill
    selected = select_skills(skills_info)
    if not selected:
        print_info("Không có skill nào được chọn. Kết thúc.")
        return
    
    # Bước 5: Kiểm tra AI nếu yêu cầu
    if args.check_ai:
        print_header("KIỂM TRA SKILL BẰNG AI")
        for skill in selected:
            print(f"\nKiểm tra skill: {skill['name']}")
            result = check_skill_with_ai(skill['full_content'])
            if result is None:
                print_info(f"Bỏ qua skill {skill['name']} do người dùng không đồng ý.")
                selected.remove(skill)
        if not selected:
            print_info("Không còn skill nào để cài.")
            return
    
    # Bước 6: Cài đặt
    target_dir = Path(args.skills_dir).expanduser()
    install_skills(selected, target_dir)
    
    # Dọn dẹp nếu dùng thư mục tạm
    if args.temp:
        print_info(f"Đang xóa thư mục tạm {clone_path}...")
        shutil.rmtree(clone_path)
    
    print_header("HOÀN TẤT")
    print_success("Các skill đã được cài đặt. Khởi động lại OpenClaw agent để áp dụng.")

if __name__ == "__main__":
    from datetime import datetime
    main()