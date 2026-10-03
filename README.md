# shopee-video-auto

Tự động hóa quy trình: nhập danh sách sản phẩm bán chạy (xuất từ Shopee
Affiliate) vào một hàng đợi bền vững → tìm clip TikTok đang viral trùng ảnh
sản phẩm → tải clip (không watermark) → đăng lên Shopee Video kèm gắn giỏ
hàng → xác nhận bài đăng thành công. Mục tiêu mặc định: 5 video/ngày, đảm
bảo không trùng lặp; sản phẩm dư trong hàng đợi tự động chuyển sang ngày
hôm sau.

## ⚠️ Rủi ro cần biết trước khi dùng

Công cụ này tải lại video TikTok của người khác và đăng lại (kèm caption +
link affiliate của bạn) trên một nền tảng khác. Đây là hành vi phổ biến
trong giới affiliate nhưng có thể vi phạm:

- **Bản quyền / ToS của TikTok**: nội dung không phải của bạn.
- **ToS chống bot của Shopee**: tự động hóa trên app điện thoại có thể khiến
  tài khoản bị hạn chế hoặc khóa.
- **Chính sách nội dung của Shopee Video**: video có thể bị gỡ nếu bị báo cáo
  trùng lặp.

Bạn đã xác nhận chấp nhận rủi ro này. Khuyến nghị: chạy với tài khoản phụ
trước, theo dõi sát trong vài ngày đầu, và có phương án dự phòng nếu tài
khoản bị hạn chế.

## Vì sao không tự động scrape Shopee Affiliate portal

Bản đầu dùng Playwright để tự bấm "Lấy link" từng sản phẩm trên portal —
nhưng chạy vài lần liên tục thì Shopee bắt nhập captcha (hệ thống chống bot
phát hiện được, dù đã kết nối qua Chrome DevTools Protocol vào cửa sổ Chrome
thật). Vì vậy bước này **không tự động hóa** nữa: bạn tự dùng chức năng có
sẵn **"Lấy link hàng loạt"** trên Shopee Affiliate (thao tác người thật, xuất
CSV), rồi nạp file đó vào hàng đợi bằng lệnh `import-csv` — không còn
Playwright/đăng nhập/CDP nào cho phần Shopee Affiliate cả.

## Kiến trúc

```
config/config.yaml              # mọi tham số + selector đều nằm ở đây
src/shopee_auto/
  config.py                     # load config.yaml -> dataclass
  state.py                      # SQLite: hàng đợi sản phẩm bền vững (pending -> video_ready -> posted)
  product_source.py             # đọc CSV "Lấy link hàng loạt"; lấy thumbnail bằng Playwright (trang SPA client-render)
  image_search.py               # Playwright: reverse image search qua Google Lens
  tiktok.py                     # yt-dlp: đo lượt xem, chọn video, tải về (đã không watermark)
  phone_control.py              # uiautomator2/ADB: điều khiển app Shopee trên điện thoại thật
  pipeline.py                   # 2 giai đoạn: prepare_videos (tìm+tải clip) và post_ready (đăng điện thoại)
  checks.py                     # kiểm tra môi trường dùng chung giữa CLI và dashboard
  cli.py                        # `shopee-auto import-csv|prepare|post|run-daily|run-once|check-setup|web`
  web.py + static/index.html    # dashboard web local điều khiển pipeline qua trình duyệt
tests/test_state.py             # unit test cho state.py (phần duy nhất test được không cần thiết bị thật)
```

Vì công cụ này chạy trên **laptop + điện thoại thật của bạn** (đăng nhập
Shopee sẵn), nó phải được cài đặt và chạy trên máy của bạn — không chạy được
trong môi trường sandbox này. Phần dưới đây là hướng dẫn cài đặt đầy đủ.

## Cài đặt

1. **Python 3.11+**, sau đó:
   ```bash
   pip install -e .
   playwright install chromium
   ```

2. **ADB + điện thoại Android**:
   - Bật *Developer options* → *USB debugging* trên điện thoại.
   - Cắm điện thoại vào laptop, xác nhận `adb devices` thấy thiết bị.
   - Cài agent uiautomator2 lên điện thoại (một lần):
     ```bash
     python -m uiautomator2 init
     ```
   - Đăng nhập sẵn Shopee trên app điện thoại (thủ công, một lần).

## Nguồn sản phẩm (CSV) và hàng đợi

Bạn chỉ cần **thả file CSV vào một thư mục** — không cần đổi tên, không cần
tự chạy lệnh nạp riêng:

1. Vào Shopee Affiliate (`https://affiliate.shopee.vn/offer/product_offer`),
   tab **"Bán chạy nhất"**, tự chọn sản phẩm muốn affiliate theo tiêu chí
   của bạn.
2. Dùng chức năng **"Lấy link hàng loạt"** → xuất file CSV (các cột:
   `Tên sản phẩm`, `Doanh thu`, `Link sản phẩm`, `Link ưu đãi`, ...). File
   có thể chứa rất nhiều dòng, có thể trùng hoặc không — không sao cả.
3. Copy nguyên file CSV vừa tải về vào thư mục `product_source.input_dir`
   trong `config/config.yaml` (mặc định `./data/input_csv`) — giữ nguyên
   tên file gốc cũng được, không cần đổi gì cả.

Mỗi lần `run-daily`/`run-once` chạy, nó **tự quét thư mục này trước**, nhận
diện file nào là mới bằng cách **hash nội dung file** (không dựa vào tên
hay thời gian sửa đổi — copy/đổi tên một file đã nạp rồi vẫn được nhận ra
là file cũ, không nạp lại), rồi tự nạp sản phẩm từ các file mới đó vào một
hàng đợi bền vững trong `data/state.db`. Muốn kiểm tra ngay việc quét mà
chưa chạy cả pipeline, dùng:
```bash
shopee-auto import-csv
```

Cơ chế hàng đợi đảm bảo:
- Một sản phẩm (theo "Link ưu đãi") chỉ được nạp **đúng 1 lần**, dù bạn thả
  bao nhiêu file chồng lặp nhau vào thư mục theo thời gian.
- Nếu hàng đợi có nhiều hơn `daily_target` sản phẩm, phần dư **tự động**
  chờ đến lượt chạy hôm sau — không cần logic "carry over" nào thêm, vì nó
  đơn giản là vẫn còn `pending` trong DB.
- Xóa file CSV khỏi thư mục sau khi đã quét không ảnh hưởng gì — hàng đợi
  đã độc lập với file.

Thumbnail sản phẩm được lấy bằng cách mở "Link sản phẩm" (trang công khai,
không cần đăng nhập) bằng trình duyệt headless (Playwright) và đọc ảnh ra
từ DOM sau khi trang render xong — gọi HTTP GET thuần không đủ vì trang sản
phẩm Shopee là SPA, ảnh chỉ xuất hiện sau khi JavaScript chạy, không có
trong HTML thô trả về ban đầu.

## Thumbnail bị Shopee chặn (verify/traffic/error)

Shopee có hệ thống chống bot riêng cho cả trang sản phẩm công khai (không
chỉ portal affiliate): nếu bạn thấy log báo `URL cuối` là
`shopee.vn/verify/traffic/error?...`, nghĩa là Shopee đã phát hiện trình
duyệt do Playwright **tự mở** và chặn ngay, bất kể trang sản phẩm đó có
thật hay không. Đây không phải lỗi tạm thời — Shopee chặn dựa trên việc
trình duyệt được khởi chạy bởi phần mềm tự động hóa, dù cố lấy ảnh kiểu gì
cũng vô ích nếu vẫn dùng trình duyệt do Playwright tự mở.

Cách khắc phục giống hệt cách đã dùng để qua chặn đăng nhập Google trước
đây: đừng để Playwright **tự mở** trình duyệt — tự tay mở một cửa sổ
Chrome thật, rồi cho Playwright **kết nối vào** cửa sổ đó sau (Chrome
DevTools Protocol). Vì trình duyệt không hề được khởi chạy bởi phần mềm tự
động hóa, Shopee không có gì để chặn (không cần đăng nhập Shopee trong cửa
sổ này — trang sản phẩm công khai, chỉ cần trông giống trình duyệt thật).

**Dashboard (`shopee-auto web`) đã có sẵn panel "Chrome để lấy ảnh sản
phẩm"** ở đầu trang — hiện đúng lệnh cần chạy (nút Copy), dòng cần thêm
vào `config.yaml`, và trạng thái kết nối theo thời gian thực (tự kiểm tra
lại mỗi 8 giây). Làm theo panel đó là đủ, không cần đọc các bước dưới đây
nữa trừ khi dùng CLI thuần:

1. Đóng hết Chrome hiện tại (kiểm tra Task Manager không còn tiến trình
   `chrome.exe` nào).
2. Mở Command Prompt, chạy (sửa đường dẫn nếu Chrome cài chỗ khác):
   ```cmd
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\shopee-chrome-profile"
   ```
   **Để nguyên cửa sổ này mở** suốt thời gian chạy "Tìm & tải video" — mỗi
   lần chạy, trình duyệt sẽ tự mở/đóng tab trong chính cửa sổ này.
3. Sửa `config/config.yaml`: đặt `product_source.cdp_endpoint: "http://localhost:9222"`,
   rồi khởi động lại `shopee-auto web` (file config chỉ đọc lúc khởi động).
4. Chạy lại "Tìm & tải video" (hoặc `shopee-auto prepare`) — lần này sẽ kết
   nối vào đúng cửa sổ Chrome bạn vừa mở, không mở cửa sổ mới nào nữa.

## Calibrating selectors (bắt buộc trước khi chạy thật)

Selector cho Google Lens và app Shopee trên điện thoại **không được đoán
trước** trong repo này — vì không có tài khoản/điện thoại thật trong môi
trường build để kiểm chứng, và Google/Shopee đổi markup định kỳ.

Bạn cần tự lấy selector thật của mình rồi điền vào `config/config.yaml`:

- **Google Lens**: dùng Playwright Codegen —
  ```bash
  playwright codegen https://lens.google.com/upload
  ```
  Upload thử 1 ảnh, click vào 1 kết quả, Codegen in ra CSS selector — copy
  vào `image_search.selectors.result_link`.

- **App Shopee trên điện thoại**: dùng uiautomator2 inspector —
  ```bash
  pip install "uiautomator2[weditor]"
  weditor
  ```
  mở `weditor`, điều hướng app Shopee tới từng màn hình trong luồng đăng
  ShopVideo, bấm vào từng nút để lấy `resourceId`/`text`/`description`, điền
  vào `phone.ui.*` trong `config/config.yaml` dạng:
  ```yaml
  ui:
    open_shop_video_entry: { resourceId: "com.shopee.vn:id/xxx" }
    caption_input: { resourceId: "com.shopee.vn:id/yyy" }
    ...
  ```

Chạy `shopee-auto check-setup` để kiểm tra Playwright/ADB/yt-dlp hoạt động,
hàng đợi/CSV còn sản phẩm hay không, và selector điện thoại đã điền chưa —
trước khi chạy `run-once`.

## Chạy

Pipeline tách làm 2 giai đoạn độc lập — chạy riêng (CLI hoặc dashboard) hoặc
gộp lại bằng `run-daily`/`run-once` cho cron:

```bash
# kiểm tra môi trường trước
shopee-auto check-setup

# (tuỳ chọn) chỉ quét thư mục input_dir và nạp file mới, không làm gì khác
shopee-auto import-csv

# Giai đoạn 1: tìm clip TikTok khớp ảnh + tải về cho từng sản phẩm đang chờ
# (không đụng điện thoại) -- tự quét input_dir trước
shopee-auto prepare

# Giai đoạn 2: đăng các video đã chuẩn bị lên điện thoại, gắn giỏ hàng,
# xác nhận -- không tìm/tải clip mới
shopee-auto post

# Gộp cả 2 giai đoạn, dùng cho cron/chạy không giám sát:
shopee-auto run-once   # đúng 1 video, kiểm tra nhanh toàn bộ luồng
shopee-auto run-daily  # đủ chỉ tiêu trong ngày (mặc định 5 video)
```

Đặt `run-daily` chạy tự động mỗi ngày bằng cron (Linux/macOS) hoặc Task
Scheduler (Windows), ví dụ cron 8h sáng mỗi ngày:
```
0 8 * * * cd /path/to/shopee-video-auto && /usr/bin/env PATH=$PATH shopee-auto run-daily >> data/logs/cron.log 2>&1
```
Không cần lịch riêng cho `import-csv`/`prepare`/`post` — `run-daily` đã gộp
cả 3 bước. Bạn chỉ cần nhớ thả file CSV vào `input_dir` trước giờ cron chạy.

## Dashboard

Thay vì gõ lệnh, có thể điều khiển toàn bộ pipeline qua trình duyệt:

```bash
shopee-auto web
```

Mở `http://127.0.0.1:8787`. Đây là server chạy ngay trên máy bạn (không
phải dịch vụ cloud, không có đăng nhập) — chỉ bạn truy cập được, vì nó cần
quyền thẳng tới ADB/điện thoại/trình duyệt trên máy này.

Trang gồm:
- **Thẻ số liệu**: số sản phẩm đang chờ chuẩn bị, đã sẵn sàng đăng, đã đăng
  hôm nay/chỉ tiêu, thất bại, đã bỏ qua.
- **3 nút thao tác**: Quét CSV mới / Tìm & tải video (giai đoạn 1) / Đăng
  video (giai đoạn 2) — mỗi nút chạy nền, có log trực tiếp ngay bên dưới,
  và tự khóa các nút khác lại trong lúc chạy (không chạy 2 việc cùng lúc
  tranh nhau điện thoại/trình duyệt).
- **Bảng quản lý hàng đợi**: lọc theo trạng thái (tab), theo tên sản phẩm
  (ô tìm kiếm, không phân biệt hoa/thường, gõ là tự lọc sau 300ms) và theo
  lượt bán tối thiểu, có **phân trang** (20 sản phẩm/trang, nút Trước/Sau +
  tổng số trang) để bảng không bị tải hết hàng nghìn dòng một lúc — đổi tab
  hoặc đổi bộ lọc sẽ tự quay về trang 1. Mỗi dòng có thể **Bỏ qua**
  (loại hẳn khỏi hàng đợi, ví dụ không muốn affiliate sản phẩm đó nữa) hoặc
  **Thử lại** (đưa về `pending` để chuẩn bị lại từ đầu, dùng cho sản phẩm bị
  lỗi). Mỗi dòng ở trạng thái "chờ chuẩn bị"/"sẵn sàng đăng" có checkbox —
  tích **1, nhiều, hoặc dùng checkbox ở đầu bảng để chọn tất cả**, rồi 2 nút
  "Tìm & tải video"/"Đăng video" sẽ chỉ chạy đúng những sản phẩm đã chọn
  (hiện số lượng ngay trên nút). Không chọn gì mà vẫn bấm nút sẽ hỏi xác
  nhận trước khi chạy cho toàn bộ — tiện để test thử vài sản phẩm nhỏ trước
  khi tin tưởng chạy hàng loạt (giảm rủi ro bị chặn/captcha khi test).
- **Trạng thái tức thời**: khi đang chạy, từng sản phẩm tự chuyển qua
  `Chờ chuẩn bị → Đang tìm video → Sẵn sàng đăng` (hoặc
  `Sẵn sàng đăng → Đang đăng → Đã đăng`) ngay trong bảng, cập nhật trực
  tiếp theo thời gian thực trong lúc job chạy, không phải đợi xong cả đợt
  mới biết.
- **Cấu hình/tài nguyên**: xem nhanh `daily_target`, thư mục CSV, ngưỡng lọc,
  gói app Shopee, số selector điện thoại đã điền — không cần mở `config.yaml`.
- **Kiểm tra môi trường**: tương đương `check-setup`, bấm 1 nút xem ngay.
- **Log gần đây**: lịch sử chi tiết từng bước của từng sản phẩm (bảng
  `run_log`), hữu ích khi cần biết chính xác sản phẩm nào lỗi ở bước nào.

Chạy `shopee-auto web --host 0.0.0.0` nếu muốn truy cập từ thiết bị khác
trong cùng mạng LAN (ví dụ xem tiến độ từ điện thoại) — cân nhắc rủi ro vì
khi đó bất kỳ ai trong mạng cũng điều khiển được pipeline (không có xác
thực).

## Chống trùng lặp & giới hạn hàng ngày

Một bảng duy nhất (`product_queue` trong `data/state.db`) theo dõi toàn bộ
vòng đời mỗi sản phẩm, dedup theo "Link ưu đãi" (UNIQUE):

```
pending ──(prepare_videos)──> video_ready ──(post_ready)──> posted
   │                               │
   └──> prepare_failed             └──> post_failed
(pending / *_failed) ──(bỏ qua thủ công)──> skipped
```

- Một sản phẩm chỉ vào hàng đợi **đúng 1 lần**, dù bạn thả bao nhiêu file
  CSV chồng lặp nhau.
- `post_ready` chỉ rút đúng phần còn thiếu của `daily_target` (trừ số đã
  `posted` trong ngày hôm nay), nên chạy `run-daily`/bấm "Đăng video" nhiều
  lần trong cùng 1 ngày không bao giờ vượt chỉ tiêu.
- Sản phẩm `prepare_failed`/`post_failed` **không tự động thử lại** (tránh 1
  sản phẩm lỗi vĩnh viễn chặn hết hàng đợi) — dùng nút "Thử lại" trên
  dashboard (hoặc `requeue_item`) để đưa về `pending` thử lại thủ công.
- Sản phẩm dư ngoài `daily_target` đơn giản vẫn còn `pending`/`video_ready`
  và được xử lý ở lượt chạy sau — đó là toàn bộ cơ chế "để dành sang hôm
  sau", không cần logic carry-over riêng.

## Xử lý sự cố thường gặp

- **Hàng đợi rỗng khi chạy `run-daily`**: chưa có file CSV nào trong
  `product_source.input_dir` — thả file xuất từ "Lấy link hàng loạt" vào
  đó rồi chạy lại (hoặc chạy `shopee-auto import-csv` để kiểm tra ngay).
- **`CaptchaEncounteredError`**: Google Lens phát hiện traffic bất thường.
  Pipeline tự bỏ qua sản phẩm đó và sang sản phẩm tiếp theo; nếu xảy ra liên
  tục, giãn `tiktok.request_delay_seconds` hoặc tạm dừng vài giờ.
- **`PhoneAutomationError: Selector 'xxx' chưa được cấu hình`**: điền selector
  còn thiếu trong `config/config.yaml` theo hướng dẫn Calibrating selectors.
