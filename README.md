# XIAO ESP32-S3 ⇄ SD NAND MKDV1GCL-ABA

Nạp toàn bộ thư mục `assets` (683 file PNG, ~3 MB) từ PC vào chip nhớ
**MKDV1GCL-ABA** (SD NAND 1 Gbit) gắn trên **Seeed Studio XIAO ESP32-S3**.

Board chạy firmware đóng vai trò "đầu ghi thẻ": PC gửi file qua USB, ESP32-S3
ghi thẳng vào SD NAND và đối chiếu CRC-32 từng file.

---

## 1. Đấu dây

MKDV1GCL-ABA là LGA-8, chân giống hệt thẻ microSD. Cách đấu dưới đây dùng
được cho **cả hai** chế độ SDIO và SPI nên không phải đổi dây khi debug.

| Chân MKDV1GCL-ABA | XIAO ESP32-S3 | GPIO | Ghi chú |
|---|---|---|---|
| 1 — DAT2 | D1 | GPIO2 | pull-up 10 kΩ lên 3V3 |
| 2 — DAT3 / CS | D3 | GPIO4 | pull-up 10 kΩ lên 3V3 |
| 3 — CMD / DI | D10 | GPIO9 | pull-up 10 kΩ lên 3V3 |
| 4 — VDD | 3V3 | — | tụ 100 nF ∥ 10 µF sát chân |
| 5 — CLK | D8 | GPIO7 | dây càng ngắn càng tốt |
| 6 — VSS | GND | — | |
| 7 — DAT0 / DO | D9 | GPIO8 | pull-up 10 kΩ lên 3V3 |
| 8 — DAT1 | D0 | GPIO1 | pull-up 10 kΩ lên 3V3 |

Năm điện trở pull-up 10 kΩ là **bắt buộc** theo chuẩn SD — pull-up nội của
ESP32 (~45 kΩ) thường đủ ở 1-bit nhưng hay lỗi ở 4-bit tốc độ cao.

Firmware tự thử lần lượt **SDIO 4-bit → SDIO 1-bit → SPI 20 MHz** và dùng
chế độ nào lên được trước.

---

## 2. Chạy — cách nhanh nhất

Cắm board vào USB rồi bấm đúp:

```
setup_and_flash.bat
```

Script làm hết: tìm `arduino-cli` (dùng lại bản có sẵn trong Arduino IDE, nếu
không có thì tự tải) → cài core ESP32 → biên dịch → nạp firmware → đẩy toàn bộ
`assets` vào SD NAND.

Máy này đã có sẵn Arduino IDE kèm `arduino-cli` và core `esp32:esp32` 3.3.3,
nên bước cài đặt sẽ bỏ qua và chạy thẳng vào biên dịch.

Chỉ định cổng nếu máy có nhiều thiết bị serial:

```
setup_and_flash.bat COM7
```

Đã nạp firmware rồi, chỉ muốn đẩy lại dữ liệu:

```
run_upload.bat
```

---

## 3. Nạp thủ công

**Firmware** — mở `ESP32S3_SDNAND_Uploader/ESP32S3_SDNAND_Uploader.ino` bằng
Arduino IDE:

- Board: `XIAO_ESP32S3` (esp32 core ≥ 2.0.11, khuyến nghị 3.x)
- USB CDC On Boot: **Enabled**
- Các mục khác để mặc định

**Dữ liệu** — từ cmd hoặc PowerShell:

```powershell
python tools\upload_assets.py
python tools\upload_assets.py --port COM7 --src "E:\...\assets" --dest /assets
```

| Tuỳ chọn | Tác dụng |
|---|---|
| `--port COM7` | chỉ định cổng (mặc định tự dò theo VID Espressif) |
| `--src <thư mục>` | thư mục nguồn trên PC |
| `--dest /assets` | thư mục đích trên thẻ |
| `--clean` | xoá sạch thư mục đích trước khi nạp |
| `--format` | format lại SD NAND rồi mới nạp |
| `--force` | ghi đè cả file đã khớp CRC |
| `--verify-only` | chỉ đối chiếu CRC, không ghi gì |
| `--retries N` | số lần thử lại mỗi file (mặc định 3) |
| `--debug` | in toàn bộ lệnh/phản hồi |

Chạy lại nhiều lần là an toàn: script hỏi CRC từng file trên thẻ, file nào
đã khớp thì bỏ qua, file nào sai hoặc thiếu thì nạp lại. Đứt giữa chừng thì
chạy lại là tiếp tục đúng chỗ dở.

> Đừng chạy từ Git Bash: MSYS sẽ biến `/assets` thành `C:/Program Files/Git/assets`.
> Script phát hiện và báo lỗi thay vì ghi nhầm.

---

## 4. Giao thức

Lệnh dạng text kết thúc bằng `\n`, mỗi lệnh trả về **đúng một** dòng `OK…`
hoặc `ER…`. Payload đi sau ở dạng nhị phân.

| Lệnh | Phản hồi |
|---|---|
| `PING` | `OK PONG <ver>` |
| `INFO` | `OK <mode> lfn=… card=… total=… used=… chunk=…` (KB) |
| `STAT <path>` | `OK <size> <crc32hex>` hoặc `OK NONE` |
| `PUT <size> <crc32hex> <path>` | `OK RDY`, rồi N chunk 2048 B (mỗi chunk ack `K`), kết `OK DONE <crc>` |
| `FORMAT` | `OK FORMATTED <mode> wiped=<n>` — xoá đệ quy mọi thứ ở gốc thẻ |
| `MKDIR` / `RM` / `RMDIR` / `LS` / `REMOUNT` / `DONE` | `OK…` / `ER…` |

`PUT` tự tạo thư mục cha. CRC-32 dùng đa thức zlib, khớp chính xác với
`zlib.crc32()` của Python; sai CRC thì firmware **xoá file** rồi báo lỗi, nên
trên thẻ không bao giờ còn file hỏng.

Ack theo từng chunk giữ lượng dữ liệu đang bay ≤ 2 KB, dưới mức buffer RX
8 KB của firmware — không bao giờ tràn, kể cả khi SD NAND đang bận ghi.

---

## 5. Đóng gói animation — `tools/anim_pack.py`

Nạp thẳng 683 file PNG lên thẻ thì firmware phải giải nén PNG lúc chạy, vừa
chậm vừa tốn RAM. `anim_pack.py` gộp trước mỗi thư mục thành **một** file ảnh
`.ANM` + **một** file tiếng `.AWV`, kèm bảng tra `ANIM.MAN`, để firmware chỉ
cần `seek` rồi đẩy thẳng ra màn hình.

```
PC (tools/assets/)                    anim_pack.py build          build/ANIM/        SD NAND
──────────────────                    ──────────────────          ───────────        ───────
assets/
├── anim.json  ────────────┐  đọc file dự án: animation nào -> folder nào
│                          │
├── batngo/                │
│   ├── frame_000.png ─┐   │
│   ├── frame_001.png  ├───┼─► sắp theo số trong tên ──► BATNGO.ANM ─┐
│   ├── ...            │   │   (natural sort, -> RGB565)              │
│   └── batngo.wav ────┴───┼─► đúng 1 file audio trong folder         ├──► /ANIM
│      (audio)             │   tự convert ──────────► BATNGO.AWV ─────┤     trên thẻ
├── boiroi/  (giống trên)  │                                          │
├── ...                    │                                          │
└── yeuthuong/             └─► ghi ANIM.MAN ──────────────────────────┘
                               (cử chỉ -> file .ANM)
```

### 5.1. Sắp xếp trên PC

```
tools/assets/
├── anim.json
├── batngo/
│   ├── frame_000.png
│   ├── frame_001.png
│   ├── ...
│   └── batngo.wav        ← đúng 1 file audio, tên gì cũng được
├── boiroi/ ...
└── yeuthuong/ ...
```

`anim.json` — sinh tự động bằng `init`, sửa tay sau đó:

```json
{
  "width": 96, "height": 64,
  "animations": [
    { "gesture": "STILL", "name": "BATNGO", "folder": "batngo", "fps": 10, "loop": true },
    { "gesture": "SHAKE", "name": "SOC",    "folder": "soc",    "fps": 20, "loop": false }
  ]
}
```

`name` là tên file trên thẻ nên phải vừa 8.3 — script tự bóp về ≤ 8 ký tự
`A-Z 0-9` và báo lại tên mới (`yeuthuong` → `YEUTHUON`), trùng thì thêm số.

### 5.2. Chạy

Bấm đúp `run_anim_pack.bat` (làm cả ba bước), hoặc từng bước:

```powershell
python tools\anim_pack.py init                 # dò assets/ -> sinh anim.json
python tools\anim_pack.py build                # -> build\ANIM\
python tools\anim_pack.py upload --port COM7   # -> /ANIM trên thẻ
python tools\anim_pack.py info build\ANIM\BATNGO.ANM
```

| Tuỳ chọn của `build` | Tác dụng |
|---|---|
| `--codec raw` | mặc định: frame RGB565 liên tiếp, firmware seek thẳng |
| `--codec rle` | nén RLE 16-bit — bộ hiện tại 8.2 MB → **2.1 MB** |
| `--byte-order le\|be` | thứ tự byte RGB565 (mặc định `le`, hợp với ESP32) |
| `--bg RRGGBB` | màu nền ghép alpha (mặc định đen) |
| `--audio-rate 16000` | ép sample rate (mặc định giữ nguyên của nguồn) |
| `--block-align 256` | kích thước block MS-ADPCM |
| `--fast-audio` | bỏ qua việc dò hệ số: nhanh hơn ~5×, kém đi ~1 dB |

`upload` chỉ gọi lại `upload_assets.py` nên vẫn có đủ CRC từng file và chạy
lại được sau khi đứt giữa chừng.

> **`upload` mặc định FORMAT cả thẻ trước khi nạp** — toàn bộ dữ liệu cũ trên
> SD NAND bị xoá, không khôi phục được. Thêm `--no-format` để giữ lại.
> `--verify-only` không bao giờ format.

### 5.3. Định dạng file

**`.ANM`** — header 40 byte little-endian, rồi dữ liệu:

| Offset | Kiểu | Trường |
|---|---|---|
| 0 | char[4] | `"ANM1"` |
| 4 / 6 | u16 | width / height |
| 8 / 10 | u16 | frame_count / fps |
| 12 | u8 | pixfmt: 0=RGB565 LE, 1=BE, 2=LE+RLE, 3=BE+RLE |
| 13 | u8 | flags, bit0 = loop |
| 14 | u16 | header_size (40) |
| 16 | u32 | frame_bytes (`w*h*2`; 0 khi RLE) |
| 20 | u32 | index_offset (0 khi raw) |
| 24 | u32 | data_offset |
| 28 | u32 | data_bytes |
| 32 | u32 | crc32 của toàn bộ phần từ `index_offset`/`data_offset` trở đi |
| 36 | u32 | dự trữ |

Raw: frame `i` nằm ở `data_offset + i*frame_bytes` — không cần bảng tra.
RLE: tại `index_offset` có `frame_count` cặp `u32 offset, u32 len` (offset
tính từ `data_offset`). Token RLE là 1 byte `n`: `n & 0x80` → `(n & 0x7F)+1`
pixel đọc thẳng; ngược lại lặp pixel kế tiếp `n+1` lần. Run tối đa 128.

**`.AWV`** — file RIFF/WAVE MS-ADPCM (`wFormatTag = 0x0002`) mono chuẩn, có
`fmt `/`fact`/`data` đầy đủ. Đổi đuôi thành `.wav` là mở được bằng trình phát
bất kỳ để nghe thử.

**`ANIM.MAN`** — text ASCII, bỏ qua dòng `#`:

```
MAN1,<width>,<height>,<số animation>
<gesture>,<name>,<anm>,<awv|->,<fps>,<loop>,<frames>,<frame_bytes>
```

---

## 6. Kiểm thử không cần phần cứng

`tools/fake_device.py` giả lập firmware qua TCP, dùng đúng giao thức trên:

```powershell
python tools\fake_device.py --root .\sandbox --port 5555
python tools\upload_assets.py --port socket://127.0.0.1:5555 --src <thư mục> --dest /assets
```

Đã chạy qua toàn bộ 683 file và đều đạt: nạp mới (683/683), chạy lại bỏ qua
toàn bộ, phát hiện file hỏng bằng CRC, tự nạp lại đúng file đó, `--clean`, và
phục hồi khi thiết bị báo lỗi giữa lúc truyền.

Firmware đã biên dịch sạch với `esp32:esp32:XIAO_ESP32S3`, core 3.3.3
(380 KB flash, 24 KB RAM — không cảnh báo nào). Core này bật sẵn
`CONFIG_FATFS_LFN_STACK` với `MAX_LFN=255` nên tên `frame_000.png` giữ nguyên.
Phần chạy trên phần cứng thật thì **chưa kiểm được** — lúc dựng chưa có board
nào cắm vào máy.

---

## 7. Gặp sự cố

| Hiện tượng | Xử lý |
|---|---|
| `sdmmc_host_clock_update_command ... returned 0x107` | `ESP_ERR_TIMEOUT` **trước khi** lệnh đầu tiên tới chip. Xem mục 7.1. |
| `khong mount duoc SD NAND` | Firmware tự chạy chẩn đoán chân ngay sau đó — đọc bảng nó in ra. |
| Mount được nhưng `lfn=0` | FATFS không bật Long File Name, tên `frame_000.png` sẽ bị cắt. Dùng esp32 core ≥ 2.0.11. |
| `khong thay firmware tra loi PING` | Serial Monitor của Arduino IDE đang giữ cổng — đóng nó lại. |
| Nạp firmware thất bại | Giữ **BOOT**, nhấn **RESET**, thả BOOT, rồi chạy lại. |
| Nhiều file `ER crc` | Dây CLK quá dài hoặc nhiễu. Giảm `SPI_FREQ_HZ`, hoặc ép 1-bit trong `mountAny()`. |
| `FORMAT` báo OK nhưng thẻ vẫn còn dữ liệu | Firmware cũ hơn bản sửa ngày 2026-09-21. `SD_MMC.begin(..., true, ...)` chỉ format khi mount **hỏng**, nên trên thẻ đang chạy tốt nó không xoá gì. Nạp lại firmware. |

### 7.1. Lỗi 0x107 lúc mount

`0x107` là `ESP_ERR_TIMEOUT`, bắn ra ở `sdmmc_host_clock_update_command` —
tức là khối CIU của SDMMC không nuốt nổi lệnh cập nhật clock, **trước cả khi**
có lệnh nào đi tới chip nhớ. Nên đây không phải lỗi chip hỏng hay sai FAT, mà
là lỗi điện ở mức đường dây.

Thủ phạm quen mặt, theo thứ tự khả năng:

1. **DAT0 (chân 7 → D9/GPIO8) bị giữ mức thấp.** CIU hiểu là thẻ đang bận
   nên treo vĩnh viễn. Do chạm mát, hàn dính, hoặc thiếu pull-up.
2. **Chip không có nguồn hoặc mất mát.** Đo 3V3 tại chân 4 và thông mạch
   GND tại chân 6 — với LGA-8 hàn tay, hai chân này rất hay không ăn thiếc.
3. **Thiếu pull-up 10 kΩ trên CMD và DAT0.**
4. **Board mở rộng Sense đang cắm.** Khe microSD trên đó dùng chung
   D8/D9/D10 nên tranh chấp bus.

Bản v1.1.0 tự chạy `diagPins()` ngay khi mount hỏng và in ra từng đường là
chạm mát / thả nổi / đã có pull-up, kèm kiểm tra chạm chập giữa các đường.
Gõ `DIAG` vào Serial Monitor để chạy lại bất cứ lúc nào.

Nếu nghi đường SDIO nhiễu, đặt `FORCE_MODE = 3` trong sketch để ép chạy SPI —
SPI dễ tính hơn nhiều và firmware còn tự lùi về 1 MHz nếu 20 MHz trượt.

Chip 1 Gbit ≈ 128 MB, dữ liệu 3 MB nên dung lượng thừa rất nhiều.
Thẻ chưa format sẽ được firmware tự format FAT khi mount thất bại — đổi
`AUTO_FORMAT_IF_UNMOUNTABLE` về `0` trong sketch nếu không muốn.

---

## 8. Cấu trúc

```
ESP32S3_SDNAND_Uploader/
    ESP32S3_SDNAND_Uploader.ino   firmware: mount SD NAND + server nhận file
tools/
    upload_assets.py              script nạp chạy trên PC
    anim_pack.py                  đóng gói assets -> .ANM/.AWV/ANIM.MAN rồi nạp
    fake_device.py                giả lập firmware để kiểm thử
    assets/                       PNG + audio nguồn, kèm anim.json
    requirements.txt
build/ANIM/                       kết quả đóng gói (không commit)
setup_and_flash.bat               làm tất cả: cài, biên dịch, nạp, đẩy dữ liệu
run_upload.bat                    chỉ đẩy lại dữ liệu thô
run_anim_pack.bat                 đóng gói animation rồi nạp
```
