# Confluence Doc Tool

Script Python độc lập (1 file duy nhất: `confluence_doc_tool.py`), dùng để:

1. **Tải hàng loạt** trang Confluence nội bộ (ví dụ `tdcconfluence.abbank.vn`)
   thành file `.doc`, dựa trên danh sách link trong 1 file `.txt` (mỗi dòng
   1 link).
2. **Gộp** toàn bộ file `.doc` trong 1 thư mục thành 1 file `.doc` duy nhất,
   format lại đồng bộ (font, cỡ chữ, hướng trang, margin, tự co bảng/ảnh).

Script được viết để **copy nguyên file `.py` sang máy cần chạy** (máy trong
mạng nội bộ, không có Internet) và chạy trực tiếp.

## Yêu cầu môi trường

| Tính năng | Yêu cầu |
|---|---|
| 1. Bulk download | Chỉ cần Python 3.8+ (dùng thư viện chuẩn `urllib`, không cần cài thêm gì). Máy chạy phải vào được mạng nội bộ có Confluence. |
| 2. Merge file .doc | Windows + **Microsoft Word đã cài sẵn** + thư viện `pywin32` (Word COM automation). |

## Cài `pywin32` khi máy đích không có Internet

Vì máy chạy script không có mạng để `pip install`, hãy chuẩn bị trước trên
máy có Internet (cùng phiên bản Python/hệ điều hành với máy đích):

```bash
pip download pywin32 -d pywin32_offline
```

Copy thư mục `pywin32_offline` cùng script sang máy đích, rồi cài offline:

```bash
pip install --no-index --find-links=pywin32_offline pywin32
python <python_dir>\Scripts\pywin32_postinstall.py -install
```

(Nếu máy đích đã có sẵn Anaconda/WinPython có kèm `pywin32`, có thể bỏ qua
bước này.)

Tính năng 1 (bulk download) **không cần bước này**, chạy được ngay cả khi
chưa cài `pywin32`.

## Cấu hình tài khoản Confluence

Mở `confluence_doc_tool.py`, sửa 2 dòng đầu:

```python
CONFLUENCE_USERNAME = "ten_dang_nhap"
CONFLUENCE_PASSWORD = "mat_khau"
```

Nếu để trống, script sẽ hỏi tài khoản/mật khẩu mỗi lần chạy (mật khẩu gõ ẩn,
không hiện lên màn hình) — an toàn hơn nếu không muốn lưu mật khẩu trong file.

## Cách chạy

```bash
python confluence_doc_tool.py
```

Menu hiện ra:

```
===== CONFLUENCE DOC TOOL =====
1. Bulk download page Confluence theo file .txt
0. Thoát
```

### 1. Bulk download

- Nhập đường dẫn tới file `.txt` chứa danh sách link Confluence (mỗi dòng 1
  link, dòng bắt đầu bằng `#` sẽ bị bỏ qua).
- Nhập thư mục để lưu file `.doc` (Enter để dùng thư mục mặc định
  `confluence_downloads` cạnh file txt).
- Script dùng đúng tính năng **"Export to Word"** có sẵn của Confluence
  (Tools → Export to Word) để tải, nên định dạng, bảng, ảnh trong trang gốc
  được giữ nguyên.
- File tải về được đặt tên `01_Tên_trang.doc`, `02_Tên_trang.doc`, ... theo
  đúng thứ tự trong file txt (để bước Merge sau này gộp đúng thứ tự).
- Cuối cùng script in ra danh sách link tải lỗi (nếu có) kèm lý do, để bạn
  kiểm tra lại (link sai, thiếu quyền xem, sai endpoint export...).

**Lưu ý:** đường dẫn export "Export to Word" có thể khác nhau tùy phiên bản
Confluence Server/Data Center. Script tự thử lần lượt vài đường dẫn phổ biến
(`/exportword`, `/exportword.action`, `/plugins/exportword/exportword.action`,
`/pages/exportword.action`). Nếu tất cả đều lỗi, có thể phiên bản Confluence
của bạn dùng đường dẫn khác — báo lại đường dẫn thật (xem trong menu Tools →
Export to Word trên trình duyệt) để bổ sung vào danh sách
`EXPORT_WORD_PATH_CANDIDATES` trong script.

### 2. Merge file .doc

- Nhập đường dẫn thư mục chứa các file `.doc` cần gộp.
- **Bắt buộc** thư mục chỉ được chứa file `.doc` (không được có `.docx`,
  `.pdf`, ...); nếu có, script báo lỗi và liệt kê để bạn dọn ra ngoài trước.
- Các file được gộp theo thứ tự tên file (a-z) — vì bước 1 đã đặt tên theo
  số thứ tự `01_`, `02_`, ... nên thứ tự gộp mặc định = thứ tự trong file
  txt ban đầu.
- File kết quả đặt tên `Merge_File_<STT>.doc` (tự tăng số thứ tự, không ghi
  đè file merge cũ), lưu ngay trong thư mục đó.
- Format áp dụng cho toàn bộ file gộp:
  - Font **Times New Roman**, cỡ **12**.
  - Hướng trang: **Landscape** (ngang).
  - Margin: **Narrow** (0.5 inch mỗi lề).
  - Ảnh và bảng rộng hơn khổ trang sẽ tự động co lại vừa với vùng in (giữ
    tỉ lệ ảnh gốc); nếu bảng/ảnh quá dài, Word sẽ tự ngắt sang trang mới
    theo chiều dọc (không bị cắt mất nội dung).

## Xử lý sự cố thường gặp

- **Toàn bộ link đều lỗi "phản hồi không hợp lệ / chưa đăng nhập"**: kiểm
  tra lại tài khoản/mật khẩu; một số Confluence dùng SSO khiến đăng nhập
  bằng form (`dologin.action`) không hoạt động — cần liên hệ IT nội bộ xác
  nhận endpoint/basic-auth có được bật hay không.
- **"Không xác định được pageId"**: link trong file txt có thể là link rút
  gọn hoặc bị sai định dạng — thử mở link đó trên trình duyệt, copy lại URL
  đầy đủ (có chứa `pageId=...` càng tốt).
- **"Chưa cài thư viện pywin32"**: xem mục "Cài `pywin32` khi máy đích
  không có Internet" ở trên.
- **Lỗi khi mở Word (COM)**: đảm bảo Microsoft Word đã cài và có thể mở
  bình thường trên máy; đóng hết các cửa sổ Word đang mở trước khi chạy lại.
