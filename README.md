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
  state.py                      # SQLite: hàng đợi sản phẩm bền vững + chống đăng trùng + đếm số video/ngày
  product_source.py             # đọc CSV "Lấy link hàng loạt", lấy thumbnail qua HTTP thuần (og:image)
  image_search.py               # Playwright: reverse image search qua Google Lens
  tiktok.py                     # yt-dlp: đo lượt xem, chọn video, tải về (đã không watermark)
  phone_control.py              # uiautomator2/ADB: điều khiển app Shopee trên điện thoại thật
  pipeline.py                   # nối toàn bộ pipeline, cô lập lỗi theo từng sản phẩm
  cli.py                        # `shopee-auto import-csv|run-daily|run-once|check-setup`
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

Thumbnail sản phẩm được lấy bằng cách gọi HTTP GET thẳng vào "Link sản phẩm"
(trang công khai, không cần đăng nhập) và đọc thẻ `og:image` — giống cách
mọi bot xem trước link (Facebook, Slack...) vẫn làm, rủi ro bị chặn rất thấp.

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

```bash
# kiểm tra môi trường trước
shopee-auto check-setup

# (tuỳ chọn) chỉ quét thư mục input_dir và nạp file mới, không đăng gì cả
shopee-auto import-csv

# đăng thử đúng 1 video để kiểm tra toàn bộ luồng (tự quét input_dir trước)
shopee-auto run-once

# chạy đủ chỉ tiêu trong ngày (mặc định 5 video, dừng sớm nếu đã đủ
# hoặc hàng đợi hết sản phẩm) -- cũng tự quét input_dir trước
shopee-auto run-daily
```

Đặt `run-daily` chạy tự động mỗi ngày bằng cron (Linux/macOS) hoặc Task
Scheduler (Windows), ví dụ cron 8h sáng mỗi ngày:
```
0 8 * * * cd /path/to/shopee-video-auto && /usr/bin/env PATH=$PATH shopee-auto run-daily >> data/logs/cron.log 2>&1
```
Không cần lịch riêng cho `import-csv` — `run-daily` đã tự quét thư mục mỗi
lần chạy. Bạn chỉ cần nhớ thả file CSV vào `input_dir` trước giờ cron chạy.

## Chống trùng lặp & giới hạn hàng ngày

Hai lớp bảo vệ độc lập trong `data/state.db` (SQLite):
- **Hàng đợi (`product_queue`)**: mỗi sản phẩm (theo "Link ưu đãi") chỉ vào
  hàng đợi đúng 1 lần dù bạn `import-csv` bao nhiêu file chồng lặp nhau,
  và mỗi ngày `run-daily` rút đúng `daily_target` sản phẩm **chưa xử lý**
  theo thứ tự vào trước, đảm bảo 1 sản phẩm không bao giờ được chọn 2 lần.
- **Lịch sử đăng bài (`posted_products`)**: `count_posted_today()` đảm bảo
  không vượt `daily_target` dù bạn chạy `run-daily` nhiều lần trong cùng
  một ngày (ví dụ sau khi sửa lỗi và chạy lại).

## Xử lý sự cố thường gặp

- **Hàng đợi rỗng khi chạy `run-daily`**: chưa có file CSV nào trong
  `product_source.input_dir` — thả file xuất từ "Lấy link hàng loạt" vào
  đó rồi chạy lại (hoặc chạy `shopee-auto import-csv` để kiểm tra ngay).
- **`CaptchaEncounteredError`**: Google Lens phát hiện traffic bất thường.
  Pipeline tự bỏ qua sản phẩm đó và sang sản phẩm tiếp theo; nếu xảy ra liên
  tục, giãn `tiktok.request_delay_seconds` hoặc tạm dừng vài giờ.
- **`PhoneAutomationError: Selector 'xxx' chưa được cấu hình`**: điền selector
  còn thiếu trong `config/config.yaml` theo hướng dẫn Calibrating selectors.
