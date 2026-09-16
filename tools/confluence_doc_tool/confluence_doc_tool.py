#!/usr/bin/env python3
"""
Confluence Doc Tool
====================
1) Tải hàng loạt trang Confluence (theo danh sách link trong 1 file .txt) về
   dạng file .doc (dùng tính năng "Export to Word" có sẵn của Confluence).
2) Gộp (merge) toàn bộ file .doc trong 1 thư mục thành 1 file .doc duy nhất,
   format lại đồng bộ: font Times New Roman cỡ 12, trang ngang (landscape),
   margin Narrow, bảng/ảnh tự co cho vừa khổ trang.

Yêu cầu:
- Toàn bộ tính năng tải (mục 1) chỉ dùng thư viện chuẩn của Python (urllib,
  http.cookiejar, re, ...) nên KHÔNG cần cài thêm gì, chạy được trong mạng
  nội bộ không có Internet.
- Tính năng gộp file (mục 2) cần chạy trên Windows có cài Microsoft Word,
  và cần thư viện `pywin32` (Word COM automation) đã được cài sẵn trên máy
  chạy script (xem README_confluence_doc_tool.md để biết cách cài offline).

Cách dùng: chạy `python confluence_doc_tool.py` rồi chọn chức năng trong menu.
"""

from __future__ import annotations

import base64
import getpass
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# CẤU HÌNH TÀI KHOẢN CONFLUENCE
# Điền trực tiếp vào đây nếu muốn (lưu ý: mật khẩu sẽ nằm dạng plain-text
# trong file này). Để trống thì mỗi lần chạy script sẽ hỏi lại.
# ---------------------------------------------------------------------------
CONFLUENCE_USERNAME = ""
CONFLUENCE_PASSWORD = ""

# Các đường dẫn (action) "Export to Word" khả dĩ tùy phiên bản Confluence.
# Script sẽ tự thử lần lượt cho tới khi có phản hồi hợp lệ.
EXPORT_WORD_PATH_CANDIDATES = [
    "/exportword",
    "/exportword.action",
    "/plugins/exportword/exportword.action",
    "/pages/exportword.action",
]

# Các dấu mốc trong URL Confluence dùng để tách "base URL" (giữ lại context
# path kiểu /confluence hoặc /wiki nếu có).
PATH_MARKERS = ("/pages/", "/display/", "/spaces/", "/x/", "/wiki/")

REQUEST_TIMEOUT = 60
INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
IGNORED_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


# ---------------------------------------------------------------------------
# Tiện ích chung
# ---------------------------------------------------------------------------
def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = INVALID_FS_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = "untitled"
    return name[:max_len]


def get_credentials() -> tuple[str, str]:
    username = CONFLUENCE_USERNAME or input("Nhập tài khoản Confluence: ").strip()
    password = CONFLUENCE_PASSWORD or getpass.getpass("Nhập mật khẩu Confluence: ")
    return username, password


def get_base_url(link: str) -> str:
    parsed = urllib.parse.urlparse(link)
    path = parsed.path
    base_path = ""
    for marker in PATH_MARKERS:
        idx = path.find(marker)
        if idx != -1:
            base_path = path[:idx]
            break
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"


def build_headers(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0 (ConfluenceDocTool; +internal script)",
    }


def build_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def try_form_login(opener, base_url: str, username: str, password: str, headers: dict) -> None:
    """Thử đăng nhập bằng form (dologin.action) để lấy session cookie.

    Nhiều Confluence Server/DC không bật HTTP Basic Auth cho các trang
    .action, nên cần có phiên đăng nhập (cookie) song song với header Basic.
    Nếu login form thất bại (SSO khác, v.v...) thì bỏ qua và vẫn thử tiếp
    bằng Basic Auth header.
    """
    login_url = f"{base_url}/dologin.action"
    data = urllib.parse.urlencode(
        {
            "os_username": username,
            "os_password": password,
            "os_destination": "",
            "login": "Log in",
        }
    ).encode("utf-8")
    req = urllib.request.Request(login_url, data=data, headers=headers)
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
            resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError):
        pass


def looks_like_login_page(content_type: str, data: bytes) -> bool:
    if "text/html" not in content_type.lower():
        return False
    snippet = data[:4000].decode("utf-8", errors="ignore").lower()
    return any(
        marker in snippet
        for marker in ("id=\"os_username\"", "name=\"os_username\"", "log in - confluence", "login.action")
    )


def is_valid_doc_response(content_type: str, data: bytes) -> bool:
    if not data:
        return False
    if looks_like_login_page(content_type, data):
        return False
    ct = content_type.lower()
    if "msword" in ct or "application/vnd" in ct or "octet-stream" in ct:
        return True
    # Confluence's "Export to Word" thực chất trả về HTML/MHTML mang đuôi .doc.
    if "text/html" in ct or "application/xml" in ct:
        return True
    return False


def extract_title(data: bytes, fallback: str) -> str:
    text = data[:20000].decode("utf-8", errors="ignore")
    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        title = title.split(" : ")[-1].strip()  # Confluence hay để "Space : Tên trang"
        if title:
            return title
    return fallback


def resolve_page_id(opener, link: str, headers: dict) -> str | None:
    parsed = urllib.parse.urlparse(link)
    qs = urllib.parse.parse_qs(parsed.query)
    if "pageId" in qs:
        return qs["pageId"][0]

    req = urllib.request.Request(link, headers=headers)
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            final_url = resp.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Không mở được link để dò pageId: {exc}") from exc

    final_qs = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)
    if "pageId" in final_qs:
        return final_qs["pageId"][0]

    m = re.search(r'name="ajs-page-id"\s+content="(\d+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'"id":"?(\d+)"?,"type":"page"', html)
    if m:
        return m.group(1)
    return None


def download_page_as_doc(opener, link: str, headers: dict, out_dir: str, index: int) -> str:
    page_id = resolve_page_id(opener, link, headers)
    if not page_id:
        raise RuntimeError("Không xác định được pageId (link có thể sai hoặc chưa đăng nhập được)")

    base_url = get_base_url(link)
    last_err = None
    for path in EXPORT_WORD_PATH_CANDIDATES:
        url = f"{base_url}{path}?pageId={page_id}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_err = f"{url} -> {exc}"
            continue

        if is_valid_doc_response(content_type, data):
            title = extract_title(data, fallback=f"page_{page_id}")
            filename = f"{index:02d}_{sanitize_filename(title)}.doc"
            out_path = os.path.join(out_dir, filename)
            with open(out_path, "wb") as f:
                f.write(data)
            return out_path
        last_err = f"{url} -> phản hồi không hợp lệ (có thể sai đường dẫn export hoặc chưa đăng nhập)"

    raise RuntimeError(last_err or "Không tải được trang (không rõ nguyên nhân)")


# ---------------------------------------------------------------------------
# Chức năng 1: Bulk download
# ---------------------------------------------------------------------------
def bulk_download_flow() -> None:
    print("\n--- TẢI HÀNG LOẠT TRANG CONFLUENCE ---")
    txt_path = input("Đường dẫn tới file .txt chứa danh sách link: ").strip().strip('"')
    if not os.path.isfile(txt_path):
        print(f"[Lỗi] Không tìm thấy file: {txt_path}")
        return

    with open(txt_path, "r", encoding="utf-8-sig") as f:
        links = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    if not links:
        print("[Lỗi] File txt không có link nào hợp lệ.")
        return

    default_out = os.path.join(os.path.dirname(os.path.abspath(txt_path)), "confluence_downloads")
    out_dir = input(f"Thư mục lưu file .doc (Enter để dùng mặc định: {default_out}): ").strip().strip('"')
    out_dir = out_dir or default_out
    os.makedirs(out_dir, exist_ok=True)

    username, password = get_credentials()
    headers = build_headers(username, password)
    opener = build_opener()

    logged_in_hosts: set[str] = set()
    ok, failed = [], []

    for i, link in enumerate(links, start=1):
        base_url = get_base_url(link)
        if base_url not in logged_in_hosts:
            try_form_login(opener, base_url, username, password, headers)
            logged_in_hosts.add(base_url)

        print(f"[{i}/{len(links)}] Đang tải: {link}")
        try:
            path = download_page_as_doc(opener, link, headers, out_dir, i)
            print(f"    -> OK: {path}")
            ok.append(link)
        except RuntimeError as exc:
            print(f"    -> LỖI: {exc}")
            failed.append((link, str(exc)))

    print(f"\nHoàn tất: {len(ok)} thành công, {len(failed)} lỗi.")
    if failed:
        print("Danh sách link lỗi:")
        for link, err in failed:
            print(f"  - {link}\n      {err}")


# ---------------------------------------------------------------------------
# Chức năng 2: Merge file .doc
# ---------------------------------------------------------------------------
def next_merge_filename(folder: str) -> str:
    pattern = re.compile(r"^Merge_File_(\d+)\.doc$", re.IGNORECASE)
    max_stt = 0
    for name in os.listdir(folder):
        m = pattern.match(name)
        if m:
            max_stt = max(max_stt, int(m.group(1)))
    return f"Merge_File_{max_stt + 1}.doc"


def collect_doc_files(folder: str) -> list[str]:
    entries = [
        name
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name)) and name.lower() not in IGNORED_FILES
    ]
    doc_files = sorted(name for name in entries if name.lower().endswith(".doc"))
    others = sorted(name for name in entries if not name.lower().endswith(".doc"))
    return doc_files, others


def apply_uniform_formatting(doc, wdc) -> None:
    doc.Content.Font.Name = "Times New Roman"
    doc.Content.Font.Size = 12

    page_setup = doc.PageSetup
    page_setup.Orientation = wdc.wdOrientLandscape
    narrow_margin = 36.0  # 0.5 inch = 36 point, tương ứng preset "Narrow" của Word
    page_setup.TopMargin = narrow_margin
    page_setup.BottomMargin = narrow_margin
    page_setup.LeftMargin = narrow_margin
    page_setup.RightMargin = narrow_margin

    usable_width = page_setup.PageWidth - page_setup.LeftMargin - page_setup.RightMargin

    for shape in doc.InlineShapes:
        try:
            if shape.Width > usable_width:
                shape.LockAspectRatio = True
                shape.Width = usable_width
        except Exception:
            pass

    for shape in doc.Shapes:
        try:
            if shape.Width > usable_width:
                shape.LockAspectRatio = True
                shape.Width = usable_width
        except Exception:
            pass

    for table in doc.Tables:
        try:
            table.PreferredWidthType = wdc.wdPreferredWidthPercent
            table.PreferredWidth = 100
            table.AutoFitBehavior(wdc.wdAutoFitWindow)
        except Exception:
            pass


def merge_doc_files(doc_paths: list[str], output_path: str) -> None:
    try:
        import win32com.client as win32
    except ImportError as exc:
        raise RuntimeError(
            "Chưa cài thư viện 'pywin32' (cần để điều khiển Microsoft Word).\n"
            "Xem README_confluence_doc_tool.md để cài offline, sau đó chạy lại."
        ) from exc

    word = win32.gencache.EnsureDispatch("Word.Application")
    from win32com.client import constants as wdc

    word.Visible = False
    word.DisplayAlerts = 0
    merged = None
    try:
        merged = word.Documents.Add()
        for i, path in enumerate(doc_paths):
            rng = merged.Content
            rng.Collapse(wdc.wdCollapseEnd)
            if i > 0:
                rng.InsertBreak(wdc.wdPageBreak)
                rng = merged.Content
                rng.Collapse(wdc.wdCollapseEnd)
            rng.InsertFile(path)

        apply_uniform_formatting(merged, wdc)
        merged.SaveAs2(output_path, FileFormat=wdc.wdFormatDocument)
    finally:
        if merged is not None:
            try:
                merged.Close(False)
            except Exception:
                pass
        word.Quit()


def merge_flow() -> None:
    print("\n--- GỘP FILE .DOC TRONG 1 THƯ MỤC ---")
    folder = input("Đường dẫn thư mục chứa các file .doc cần gộp: ").strip().strip('"')
    if not os.path.isdir(folder):
        print(f"[Lỗi] Không tìm thấy thư mục: {folder}")
        return

    doc_files, other_files = collect_doc_files(folder)
    if other_files:
        print("[Lỗi] Thư mục đang chứa file KHÔNG phải .doc, vui lòng bỏ ra ngoài trước khi gộp:")
        for name in other_files:
            print(f"  - {name}")
        return

    if not doc_files:
        print("[Lỗi] Không có file .doc nào trong thư mục.")
        return

    print(f"Tìm thấy {len(doc_files)} file .doc, thứ tự gộp:")
    for name in doc_files:
        print(f"  - {name}")

    output_name = next_merge_filename(folder)
    output_path = os.path.join(folder, output_name)
    doc_paths = [os.path.join(folder, name) for name in doc_files]

    print(f"Đang gộp thành: {output_path} ...")
    try:
        merge_doc_files(doc_paths, output_path)
    except RuntimeError as exc:
        print(f"[Lỗi] {exc}")
        return
    except Exception as exc:  # lỗi COM/Word bất ngờ
        print(f"[Lỗi] Gộp file thất bại: {exc}")
        return

    print(f"Hoàn tất! File gộp: {output_path}")


# ---------------------------------------------------------------------------
# Menu chính
# ---------------------------------------------------------------------------
def main() -> None:
    while True:
        print("\n===== CONFLUENCE DOC TOOL =====")
        print("1. Bulk download page Confluence theo file .txt")
        print("2. Merge toàn bộ file .doc trong 1 folder")
        print("0. Thoát")
        choice = input("Chọn chức năng: ").strip()

        if choice == "1":
            bulk_download_flow()
        elif choice == "2":
            merge_flow()
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("Lựa chọn không hợp lệ, vui lòng chọn 1, 2 hoặc 0.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng theo yêu cầu người dùng.")
        sys.exit(1)
