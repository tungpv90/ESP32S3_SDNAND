# tools_2: đóng gói bộ mặt Mochi (startup, default, mặt thường, gyro)

Bản mở rộng của `tools/`. Thư mục `tools/` giữ nguyên, không bị sửa gì. So với bản cũ, `tools_2/anim_pack.py`
biết thêm:
- `startup/` và `default/`;
- thư mục `gyro/` cùng file `GYRO.MAN`;
- dòng `P` lấy từ `params` / `gyro_params` trong `anim.json`;
- fps mặc định là 30.

`upload_assets.py`, `fake_device.py` và firmware ESP32 dùng lại nguyên bản. Uploader vốn đã nạp cả thư mục con.

## Chạy

Bấm đúp `tools_2\run_anim_pack.bat` để đóng gói rồi nạp. Hoặc chạy từng bước từ gốc repo:

```powershell
python tools_2\anim_pack.py build                 # -> build\tools_2\ANIM\ (có GYRO\)
python tools_2\anim_pack.py upload --port COM7    # -> /ANIM trên thẻ (mặc định FORMAT thẻ trước)
python tools_2\anim_pack.py info build\tools_2\ANIM\GYRO\GYRO.MAN
```

Mỗi lần `build` sẽ xoá các file `.ANM`, `.AWV`, `.MAN` cũ trong thư mục đích, để không đẩy phim cũ lên thẻ.

## Tổ chức `assets/`

```
assets/
  anim.json
  startup/          phát 1 lần lúc khởi động       -> STARTUP
  default/          mặt mặc định, phát lặp          -> DEFAULT
  <mặt>/            mặt thường                      -> playlist
  gyro/
    left/           mặt khi CUA TRÁI
    right/          mặt khi CUA PHẢI
    accel/          mặt khi TĂNG TỐC
    brake/          mặt khi PHANH
    bump/           mặt khi XÓC
```

- Mỗi thư mục mặt chứa `frame_*.png` và **tối đa 1** file âm thanh.
- Thư mục có tên bắt đầu bằng `_` hoặc `.` sẽ bị bỏ qua.

Bộ `assets/` hiện có sinh từ `D:\Github\Create_AI_VIDEO\make_assets.py`. Cụ thể là 62 mặt mochi3 cùng 10 mặt
gyro, 30fps, âm thanh mono 16kHz. Các mặt gyro được chia vào nhóm tùy ý, chỉ để test.

### Sinh `anim.json`

```powershell
python tools_2\anim_pack.py init --force        # quét lại toàn bộ assets/ (ghi đè anim.json)
python tools_2\anim_pack.py init --gyro-only    # chỉ quét lại assets/gyro/, giữ nguyên các phần khác
```

Các khoá trong `anim.json`:

| Khoá | Ý nghĩa |
|---|---|
| `startup` / `default` | `{name, folder, fps, loop}` |
| `animations[]` | mặt thường, giống bản cũ (`gesture`, `name`, `folder`, `fps`, `loop`) |
| `params` | ghi thành các dòng `P` trong `ANIM.MAN`, ví dụ `{"STILL_MS": 1500}` |
| `gyro[]` | như `animations[]`, nhưng thay `gesture` bằng `group`: `"LEFT"` hoặc một mảng như `["BRAKE", "BUMP"]` |
| `gyro_params` | ghi thành các dòng `P` trong `GYRO.MAN`, ví dụ `{"FWD_AXIS": "-Y", "REACT_COOLDOWN_MS": 30000}` |

`--gyro-only` sinh lại các nhóm theo tên thư mục. Nhóm nào đã sửa tay thành mảng thì phải sửa lại sau khi chạy.

## Trên thẻ

```
/ANIM/
  ANIM.MAN                  STARTUP, DEFAULT, rồi các mặt thường
  STARTUP.ANM  DEFAULT.ANM  <MẶT>.ANM  <MẶT>.AWV ...
  GYRO/
    GYRO.MAN                cột 1 = nhóm; mặt thuộc nhiều nhóm thì lặp dòng
    <MẶT>.ANM  <MẶT>.AWV ...
```

`ANIM.MAN` và `GYRO.MAN` có cùng các cột CSV như bản cũ, cộng thêm các dòng `P KEY VALUE` ở cuối file:

```
MAN1,96,64,10
LEFT,HATSPARK,HATSPARK.ANM,HATSPARK.AWV,30,0,159,12288
...
P FWD_AXIS -Y
P REACT_COOLDOWN_MS 30000
```

Bộ hiện tại có 10.394 khung, tổng khoảng **125MB**, nên cần thẻ 256MB.

## Firmware AC6966B chưa theo kịp

Bộ dữ liệu này **không** được làm để khớp với firmware hiện tại. Để phát được đầy đủ, phía `ac696x_soundbox_sdk`
cần:
- nâng `ANIM_MAX_CLIPS` (hiện là 24) và `ANIM_MANIFEST_MAX` (hiện là 2KB; `ANIM.MAN` giờ khoảng 3.8KB);
- sửa `anim_period_ms()`, vì nó đang làm tròn chu kỳ 33ms của 30fps thành 40ms;
- đọc được `STARTUP` / `DEFAULT` / `GYRO/GYRO.MAN` và các tham số `P` mới.
