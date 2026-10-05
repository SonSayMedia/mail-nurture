# Mail Nurture

App desktop (Windows) **nuôi tài khoản Google** cho các profile đã đăng nhập sẵn trên **GPM Login**:
mỗi phiên tool mở profile qua API của GPM, rồi dùng tài khoản như một người bình thường — xem YouTube,
đọc báo, đọc Gmail, viết nhật ký — với nhịp độ, thứ tự và thao tác ngẫu nhiên, mỗi ngày mỗi khác.

> ⚠️ Chỉ dùng cho **tài khoản của chính bạn**. Tự động hoá trình duyệt có thể trái điều khoản của Google;
> bạn tự chịu trách nhiệm khi sử dụng. Tool được thiết kế để mô phỏng hoạt động bình thường, **không** dùng
> để cày view / like / subscribe hay thao túng chỉ số.

## Tính năng

- **YouTube** 2–4 video mỗi phiên, lấy từ tìm kiếm / trang chủ / cột gợi ý bên phải; mỗi video xem 2–3 phút
  với tối đa 3 thao tác (tạm dừng, tua tới/lùi, bấm thanh tiến trình, xem lại); **tự bấm "Bỏ qua quảng cáo"**
  và ghi lại số lần.
- **Đọc báo** 2–3 phút, thời gian đọc mỗi bài tuỳ độ dài bài. **Đọc Gmail** — chỉ đọc, không xoá/gửi/trả lời.
- **Nhật ký Google Docs** ≥500 từ do AI viết (qua 9router), **gõ từng phím** như người thật (70–90 từ/phút,
  có gõ nhầm rồi sửa), nối vào một file "Nhật ký" duy nhất của mỗi mail.
- Phiên **15–20 phút**, **luân phiên** công bằng, mỗi mail **tối đa 1 lần/ngày**; khởi động dần cho mail mới.
- Quản lý theo **nhóm GPM**: Nuôi / **Khóa** (không bao giờ mở) / Để sau; thị trường – ngôn ngữ
  (Việt, Nhật, Anh-Mỹ, Hàn, Mỹ Latinh), sở thích, website và từ khoá chỉ định riêng từng nhóm.
- **Lá chắn an toàn**: gặp trang đăng nhập / xác minh / captcha của Google là dừng ngay, không thao tác,
  cho mail nghỉ 48 giờ. Kiểm tra đăng nhập chỉ bằng cookie.
- Trang **Nhật ký & Lỗi**: việc cần xử lý, lịch sử từng phiên, nhật ký theo ngày.
- **Chạy nhiều máy** cùng một bộ profile GPM (qua thư mục chia sẻ trong mạng LAN) mà không nuôi trùng.

## Yêu cầu

- Windows 10/11.
- [Python 3.10+](https://www.python.org/downloads/) — khi cài nhớ tích **"Add python.exe to PATH"**.
- **GPM Login** (bản dùng API `/api/v3`), các profile **đã đăng nhập sẵn tài khoản Google**.
- (Tuỳ chọn) **9router** chạy trên máy để AI viết nhật ký — cần cho nhật ký ≥500 từ và các thị trường ngoài
  tiếng Việt. Không có thì tool vẫn nuôi bình thường, chỉ bỏ qua bước viết nhật ký.
- (Tuỳ chọn) **YouTube Data API v3 key** để chọn video sát sở thích / vùng hơn.

## Cài đặt

1. Tải bản mới nhất ở mục **Releases** (file `mail-nurture-v….zip`) rồi giải nén — hoặc `git clone` repo này.
2. Bấm đúp **`MAIL NURTURE APP.bat`**. Lần đầu nó tự tạo môi trường Python, cài thư viện (cần Internet)
   và tạo `config.yaml` từ `config.example.yaml`. Các lần sau mở app ngay.
   (Không cần `playwright install` — tool bám vào trình duyệt do GPM mở.)

## Thiết lập lần đầu

1. **GPM Login**: vào *Cài đặt → API*, **bật API** (mặc định cổng `19995`). Xếp các profile vào **nhóm**,
   ví dụ một nhóm cho mail cần nuôi, một nhóm cho mail đang làm kênh.
2. Mở app → góc trái dưới phải hiện **"Đã kết nối GPM · N mail"**. Nếu GPM chạy cổng khác, sửa ở
   *tab Cấu hình → Nâng cao → Địa chỉ API của GPM*.
3. **Tab Nhóm mail**: với từng nhóm GPM của bạn chọn **Nuôi**, **Khóa** (tool không bao giờ mở) hoặc
   **Để sau**. Thay đổi được lưu tự động. Nhóm mới luôn mặc định *Để sau* để an toàn.
   Nút **Tuỳ chỉnh nhóm** cho phép đặt thị trường – ngôn ngữ, sở thích, website / từ khoá YouTube riêng.
4. **Tab Cấu hình** (tuỳ chọn): dán **Token 9router**, chọn *Nguồn viết nhật ký = 9router*, dán
   **YouTube API key** (tự lưu) → bấm **Kiểm tra kết nối** để thử GPM, 9router và YouTube API.
5. **Bảng điều khiển → Kiểm tra đăng nhập**: mail nào hiện "● Không" thì đăng nhập lại trong GPM trước.

## Sử dụng hằng ngày

- Bấm **Chạy đợt nhẹ** (trong khung giờ hoạt động, mặc định 8h–22h): tool tự chọn khoảng 40% số mail
  thuộc nhóm Nuôi — ưu tiên mail lâu chưa được nuôi — và chạy lần lượt từng mail, mỗi mail 15–20 phút.
- Hoặc tích chọn mail rồi bấm **Nuôi N mail đã chọn**.
- Xem tab **Nhật ký & Lỗi** để biết mail nào cần xử lý (đăng xuất, không mở được, bị Google cảnh báo…).
- **Thêm mail / nhóm mới**: chỉ cần thêm trong GPM rồi bấm **Làm mới** trong app.

Chi tiết từng nút: **`HƯỚNG DẪN SỬ DỤNG.txt`**. Tool làm gì trong mỗi phiên và phần "tự học":
**`QUY TRÌNH TƯƠNG TÁC MAIL.txt`**.

## Dữ liệu riêng của bạn

`config.yaml` (token, API key, cài đặt nhóm) và thư mục `data/` (lịch sử nuôi, nhật ký tool) chỉ nằm trên
máy bạn và đã được chặn trong `.gitignore` — đừng đưa chúng lên GitHub hay gửi cho người khác.
Muốn "reset" lịch sử nuôi: xoá `data\activity.db`.

## Dòng lệnh (tuỳ chọn)

Bấm **`CHAY MAIL NURTURE.bat`** để mở menu, hoặc dùng trực tiếp:

| Lệnh | Việc |
|------|------|
| `python main.py check` | Kiểm tra kết nối GPM |
| `python main.py profiles` | Liệt kê profile trong GPM |
| `python main.py check-login [id ...]` | Kiểm tra mail còn đăng nhập Google (chỉ đọc cookie) |
| `python main.py run-once <profile_id>` | Nuôi thử 1 profile (vẫn giới hạn 1 phiên/ngày) |
| `python main.py daily --dry-run` | Xem hôm nay sẽ nuôi những profile nào |
| `python main.py daily` | Chạy một đợt nuôi |

Muốn chạy tự động, dùng **Task Scheduler** của Windows gọi
`<thư mục cài>\.venv\Scripts\python.exe main.py daily` vài lần/ngày trong khung giờ hoạt động — tool tự
đảm bảo mỗi mail chỉ chạy 1 phiên/ngày.

## Cấu hình chính (`config.yaml`)

Phần lớn chỉnh được trong tab **Cấu hình**; các mục còn lại xem chú thích trong `config.example.yaml`.

- `run.concurrency`, `run.daily_fraction`, `run.active_hours` — chạy mấy mail cùng lúc, tỉ lệ mail/ngày, giờ hoạt động.
- `session.total_minutes` — thời lượng mỗi phiên (mặc định 15–20 phút).
- `youtube.*` — số video, thời gian xem, số thao tác, bỏ qua quảng cáo.
- `news.sites`, `news.read_seconds` — trang báo và thời gian đọc.
- `diary.min_words`, `diary.typing`, `diary.typing_wpm` — độ dài nhật ký và cách gõ.
- `safety.*` — giới hạn 1 phiên/ngày, nghỉ 48h khi bị cảnh báo, dừng khẩn khi lỗi liên tiếp.
- `shared.dir` — thư mục chung khi chạy nhiều máy.

## Cấu trúc

```
app.py               App desktop (CustomTkinter)
main.py              Dòng lệnh
config.example.yaml  Cấu hình mẫu (app tự tạo config.yaml từ file này)
src/
  gpm_client.py      API GPM Login v3
  browser.py         Bám Playwright vào trình duyệt GPM (CDP)
  runner.py          Chạy 1 phiên cho 1 mail
  scheduler.py       Chọn mail mỗi ngày, luân phiên, chạy theo đợt
  actions/           youtube · news · gmail · diary · visit
  humanize.py        Gõ / cuộn / di chuột / nghỉ giống người
  personas.py        "Tính cách" ổn định cho từng mail
  routine.py         Trình tự công việc mỗi ngày một khác
  warmup.py          Khởi động dần theo "tuổi" mail
  safety.py          Nhận diện trang đăng nhập / xác minh / chặn bot
  login_check.py     Kiểm tra đăng nhập các mail
  groups.py          Chế độ nhóm + cấu hình riêng từng nhóm
  markets.py         Thị trường – ngôn ngữ
  content.py         Sinh nội dung nhật ký (9router / Gemini / Claude / mẫu)
  conn_check.py      Nút "Kiểm tra kết nối"
  shared.py          Phối hợp nhiều máy
  store.py           Lịch sử (SQLite: data/activity.db)
  monitor.py, activity_log.py   Trang Nhật ký & Lỗi
```
