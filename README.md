# shopee-video-auto

Tự động hóa quy trình: tìm sản phẩm bán chạy trên Shopee Affiliate → tìm clip
TikTok đang viral trùng ảnh sản phẩm → tải clip (không watermark) → đăng lên
Shopee Video kèm gắn giỏ hàng → xác nhận bài đăng thành công. Mục tiêu mặc
định: 10 video/ngày.

## ⚠️ Rủi ro cần biết trước khi dùng

Công cụ này tải lại video TikTok của người khác và đăng lại (kèm caption +
link affiliate của bạn) trên một nền tảng khác. Đây là hành vi phổ biến
trong giới affiliate nhưng có thể vi phạm:

- **Bản quyền / ToS của TikTok**: nội dung không phải của bạn.
- **ToS chống bot của Shopee & Google**: đăng nhập/thao tác tự động có thể
  khiến tài khoản bị hạn chế hoặc khóa.
- **Chính sách nội dung của Shopee Video**: video có thể bị gỡ nếu bị báo cáo
  trùng lặp.

Bạn đã xác nhận chấp nhận rủi ro này. Khuyến nghị: chạy với tài khoản phụ
trước, theo dõi sát trong vài ngày đầu, và có phương án dự phòng nếu tài
khoản bị hạn chế.

## Kiến trúc

```
config/config.yaml              # mọi tham số + selector đều nằm ở đây
src/shopee_auto/
  config.py                     # load config.yaml -> dataclass
  state.py                      # SQLite: chống đăng trùng sản phẩm, đếm số video/ngày
  shopee_affiliate.py           # Playwright (profile đăng nhập sẵn): scrape sản phẩm bán chạy
  image_search.py               # Playwright: reverse image search qua Google Lens
  tiktok.py                     # yt-dlp: đo lượt xem, chọn video, tải về (đã không watermark)
  phone_control.py              # uiautomator2/ADB: điều khiển app Shopee trên điện thoại thật
  pipeline.py                   # nối toàn bộ pipeline, cô lập lỗi theo từng sản phẩm
  cli.py                        # `shopee-auto run-daily|run-once|check-setup`
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

3. **Đăng nhập Shopee Affiliate trên laptop** (một lần):
   - Sửa `shopee_affiliate.headless: false` trong `config/config.yaml`.
   - Chạy `shopee-auto check-setup` hoặc mở thử `run-once` — một cửa sổ
     Chromium sẽ mở, bạn đăng nhập Shopee Affiliate thủ công. Phiên đăng
     nhập được lưu vào `data/browser_profile/` (persistent context) nên các
     lần chạy sau không cần đăng nhập lại.

## Calibrating selectors (bắt buộc trước khi chạy thật)

Selector cho Shopee Affiliate portal, Google Lens, và app Shopee trên điện
thoại **không được đoán trước** trong repo này — vì:
- Không có tài khoản Shopee/điện thoại thật trong môi trường build để kiểm
  chứng.
- Shopee/Google đổi markup định kỳ, hard-code selector sẽ sớm lỗi thời.

Bạn cần tự lấy selector thật của mình rồi điền vào `config/config.yaml`:

- **Web (Shopee Affiliate portal, Google Lens)**: dùng Playwright Codegen —
  ```bash
  playwright codegen https://affiliate.shopee.vn/offer/product_pool
  ```
  thao tác thử filter/sort/click sản phẩm, Codegen sẽ in ra CSS selector
  tương ứng để bạn copy vào `shopee_affiliate.selectors` / `image_search.selectors`.

- **App Shopee trên điện thoại**: dùng uiautomator2 inspector —
  ```bash
  python -m uiautomator2 --help   # xem lệnh `uidump` / weditor
  weditor
  ```
  mở `weditor` (chạy `pip install uiautomator2[weditor]` nếu chưa có), điều
  hướng app Shopee tới từng màn hình trong luồng đăng ShopVideo, bấm vào từng
  nút để lấy `resourceId`/`text`/`description`, điền vào `phone.ui.*` trong
  `config/config.yaml` dạng:
  ```yaml
  ui:
    open_shop_video_entry: { resourceId: "com.shopee.vn:id/xxx" }
    caption_input: { resourceId: "com.shopee.vn:id/yyy" }
    ...
  ```

Chạy `shopee-auto check-setup` để kiểm tra Playwright/ADB/yt-dlp hoạt động và
cảnh báo nếu selector còn thiếu, trước khi chạy `run-once`.

## Google chặn đăng nhập ("Couldn't sign you in")

Nếu tài khoản Shopee của bạn đăng nhập qua Google, bạn sẽ gặp trang
"This browser or app may not be secure" khi bấm nút Google trong cửa sổ
Playwright mở ra. Đây không phải lỗi tạm thời — Google **luôn** chặn đăng
nhập OAuth từ trình duyệt tự động hóa (banner "Chrome is being controlled by
automated test software"), nên đăng nhập lại trong đó sẽ không bao giờ
thành công.

Cách khắc phục đúng: đừng để Playwright **tự mở và điều khiển** trình duyệt
ngay từ đầu (đó là cái Google phát hiện được) — thay vào đó, tự tay mở một
cửa sổ Chrome thật, tự tay đăng nhập, rồi mới cho Playwright **kết nối vào**
cửa sổ đó sau (Chrome DevTools Protocol / CDP). Vì đăng nhập diễn ra hoàn
toàn thủ công, Google không có gì để chặn.

> Bản thử trước dùng cách copy thư mục hồ sơ Chrome (`channel: "chrome"` +
> copy `Default/`) — cách đó dễ gặp lỗi "Sharing violation" vì Windows/Chrome
> vẫn giữ khóa file `Cookies` ngay cả khi tưởng đã đóng hết cửa sổ. Dùng CDP
> bên dưới ổn định hơn hẳn, khuyến khích dùng cách này thay thế.

1. Đóng hết Chrome hiện tại (Task Manager → kiểm tra không còn tiến trình
   `chrome.exe` nào, tắt hết nếu có — Chrome hay chạy ngầm dù đã đóng cửa
   sổ).
2. Mở Command Prompt, chạy (sửa đường dẫn nếu Chrome cài chỗ khác):
   ```cmd
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\shopee-chrome-profile"
   ```
   Một cửa sổ Chrome **hoàn toàn bình thường** hiện ra (không có banner
   "controlled by automated software" vì đây chưa phải Playwright điều
   khiển). `--user-data-dir` trỏ tới thư mục mới để tách biệt, không đụng
   vào hồ sơ Chrome bạn dùng hàng ngày.
3. Trong cửa sổ đó, vào `https://affiliate.shopee.vn/offer/product_pool`,
   đăng nhập Shopee (kể cả qua Google) như bình thường. **Giữ nguyên cửa sổ
   này mở**, đừng tắt.
4. Sửa `config/config.yaml`: đặt `shopee_affiliate.cdp_endpoint: "http://localhost:9222"`.
5. Chạy lại pipeline (`check-setup`, `run-once`...) — nó sẽ tự kết nối vào
   đúng cửa sổ Chrome bạn vừa mở ở bước 2, dùng session đã đăng nhập sẵn,
   không mở cửa sổ mới nào nữa.

Lưu ý: mỗi lần muốn chạy `run-once`/`run-daily`, cửa sổ Chrome ở bước 2 phải
đang mở sẵn (chạy lại lệnh ở bước 2 nếu bạn đã tắt máy/đóng Chrome — session
đăng nhập vẫn còn vì nó nằm trong `C:\shopee-chrome-profile`, bạn không cần
đăng nhập lại, chỉ cần mở lại đúng lệnh đó).

## Chạy

```bash
# kiểm tra môi trường trước
shopee-auto check-setup

# đăng thử đúng 1 video để kiểm tra toàn bộ luồng
shopee-auto run-once

# chạy đủ chỉ tiêu trong ngày (mặc định 10 video, dừng sớm nếu đã đủ
# hoặc hết sản phẩm phù hợp)
shopee-auto run-daily
```

Đặt `run-daily` chạy tự động mỗi ngày bằng cron (Linux/macOS) hoặc Task
Scheduler (Windows), ví dụ cron 8h sáng mỗi ngày:
```
0 8 * * * cd /path/to/shopee-video-auto && /usr/bin/env PATH=$PATH shopee-auto run-daily >> data/logs/cron.log 2>&1
```

## Chống trùng lặp & giới hạn hàng ngày

`data/state.db` (SQLite) ghi lại mọi sản phẩm đã đăng thành công — pipeline
sẽ không bao giờ chọn lại cùng một sản phẩm, và `count_posted_today()` đảm
bảo không vượt `daily_target` dù bạn chạy `run-daily` nhiều lần trong cùng
một ngày (ví dụ sau khi sửa lỗi và chạy lại).

## Xử lý sự cố thường gặp

- **`CaptchaEncounteredError`**: Google Lens phát hiện traffic bất thường.
  Pipeline tự bỏ qua sản phẩm đó và sang sản phẩm tiếp theo; nếu xảy ra liên
  tục, giãn `tiktok.request_delay_seconds` hoặc tạm dừng vài giờ.
- **`PhoneAutomationError: Selector 'xxx' chưa được cấu hình`**: điền selector
  còn thiếu trong `config/config.yaml` theo hướng dẫn Calibrating selectors.
- **`NotLoggedInError`**: phiên đăng nhập Shopee Affiliate hết hạn hoặc
  profile bị xóa — chạy lại với `headless: false` và đăng nhập lại thủ công.
