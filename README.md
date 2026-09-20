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
| `MKDIR` / `RM` / `RMDIR` / `LS` / `FORMAT` / `REMOUNT` / `DONE` | `OK…` / `ER…` |

`PUT` tự tạo thư mục cha. CRC-32 dùng đa thức zlib, khớp chính xác với
`zlib.crc32()` của Python; sai CRC thì firmware **xoá file** rồi báo lỗi, nên
trên thẻ không bao giờ còn file hỏng.

Ack theo từng chunk giữ lượng dữ liệu đang bay ≤ 2 KB, dưới mức buffer RX
8 KB của firmware — không bao giờ tràn, kể cả khi SD NAND đang bận ghi.

---

## 5. Kiểm thử không cần phần cứng

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

## 6. Gặp sự cố

| Hiện tượng | Xử lý |
|---|---|
| `sdmmc_host_clock_update_command ... returned 0x107` | `ESP_ERR_TIMEOUT` **trước khi** lệnh đầu tiên tới chip. Xem mục 6.1. |
| `khong mount duoc SD NAND` | Firmware tự chạy chẩn đoán chân ngay sau đó — đọc bảng nó in ra. |
| Mount được nhưng `lfn=0` | FATFS không bật Long File Name, tên `frame_000.png` sẽ bị cắt. Dùng esp32 core ≥ 2.0.11. |
| `khong thay firmware tra loi PING` | Serial Monitor của Arduino IDE đang giữ cổng — đóng nó lại. |
| Nạp firmware thất bại | Giữ **BOOT**, nhấn **RESET**, thả BOOT, rồi chạy lại. |
| Nhiều file `ER crc` | Dây CLK quá dài hoặc nhiễu. Giảm `SPI_FREQ_HZ`, hoặc ép 1-bit trong `mountAny()`. |

### 6.1. Lỗi 0x107 lúc mount

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

## 7. Cấu trúc

```
ESP32S3_SDNAND_Uploader/
    ESP32S3_SDNAND_Uploader.ino   firmware: mount SD NAND + server nhận file
tools/
    upload_assets.py              script nạp chạy trên PC
    fake_device.py                giả lập firmware để kiểm thử
    requirements.txt
setup_and_flash.bat               làm tất cả: cài, biên dịch, nạp, đẩy dữ liệu
run_upload.bat                    chỉ đẩy lại dữ liệu
```
