/*
 * ESP32S3_SDNAND_Uploader
 * -----------------------
 * Seeed Studio XIAO ESP32-S3  <->  MKDV1GCL-ABA (1 Gbit SD NAND, LGA-8)
 *
 * Nhiem vu: mount SD NAND roi lam "server" nhan file qua USB Serial,
 * de script tren PC (tools/upload_assets.py) day nguyen thu muc assets vao.
 *
 * SO DO CHAN (mot cach dau day dung cho CA hai che do SDIO va SPI)
 * ---------------------------------------------------------------
 *   MKDV1GCL-ABA          XIAO ESP32-S3        Ghi chu
 *   1  DAT2               D1  / GPIO2          pull-up 10k len 3V3
 *   2  DAT3 / CS          D3  / GPIO4          pull-up 10k len 3V3
 *   3  CMD  / DI (MOSI)   D10 / GPIO9          pull-up 10k len 3V3
 *   4  VDD                3V3                  tu 100nF + 10uF sat chan
 *   5  CLK  / SCLK        D8  / GPIO7          day cang ngan cang tot
 *   6  VSS                GND
 *   7  DAT0 / DO (MISO)   D9  / GPIO8          pull-up 10k len 3V3
 *   8  DAT1               D0  / GPIO1          pull-up 10k len 3V3
 *
 * Sketch tu do thu lan luot: SDIO 4-bit -> SDIO 1-bit -> SPI.
 *
 * CAI DAT TRONG ARDUINO IDE
 *   Board            : XIAO_ESP32S3   (esp32 core >= 2.0.11, khuyen nghi 3.x)
 *   USB CDC On Boot  : Enabled
 *   Flash/PSRAM      : de mac dinh
 */

#include <Arduino.h>
#include <FS.h>
#include <SD_MMC.h>
#include <SPI.h>
#include <SD.h>
#include <esp_log.h>

// ----------------------------------------------------------------- cau hinh
#define PIN_CLK 7   // D8
#define PIN_CMD 9   // D10  (MOSI khi chay SPI)
#define PIN_D0  8   // D9   (MISO khi chay SPI)
#define PIN_D1  1   // D0
#define PIN_D2  2   // D1
#define PIN_D3  4   // D3   (CS khi chay SPI)
#define PIN_CS  PIN_D3

#define SPI_FREQ_HZ  20000000UL  // 20 MHz cho che do SPI du phong
#define SDMMC_KHZ    20000       // 20 MHz cho che do SDIO
#define CHUNK_SIZE  2048         // phai khop voi CHUNK trong upload_assets.py
#define RX_BUF_SIZE 8192

// The SD NAND moi chua format thi khong mount duoc. Dat = 1 de sketch tu
// format FAT khi mount that bai. Dat = 0 neu so mat du lieu (luc do dung
// lenh FORMAT gui tu PC khi can).
#define AUTO_FORMAT_IF_UNMOUNTABLE 1

// Ep cung mot che do khi go loi:
//   0 = tu do (4-bit -> 1-bit -> SPI)
//   1 = chi SDIO 4-bit     2 = chi SDIO 1-bit     3 = chi SPI
#define FORCE_MODE 0

#define FW_VERSION "1.1.0"

// ----------------------------------------------------------------- trang thai
static fs::FS *gfs = nullptr;
static const char *gMode = "none";
static bool gLfn = false;
static uint8_t gBuf[CHUNK_SIZE];
static uint32_t gCrcTable[256];

// ----------------------------------------------------------------- tien ich
static void crcInit() {
  for (uint32_t i = 0; i < 256; i++) {
    uint32_t c = i;
    for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320UL ^ (c >> 1)) : (c >> 1);
    gCrcTable[i] = c;
  }
}
static inline uint32_t crcUpdate(uint32_t crc, const uint8_t *p, size_t n) {
  while (n--) crc = gCrcTable[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
  return crc;
}
// CRC-32 (zlib) -> khop chinh xac voi zlib.crc32() cua Python
static inline uint32_t crcBegin() { return 0xFFFFFFFFUL; }
static inline uint32_t crcFinish(uint32_t crc) { return crc ^ 0xFFFFFFFFUL; }

static void ok(const String &msg = String()) {
  Serial.print("OK");
  if (msg.length()) { Serial.print(' '); Serial.print(msg); }
  Serial.print('\n');
  Serial.flush();
}
static void err(const String &msg) {
  Serial.print("ER ");
  Serial.print(msg);
  Serial.print('\n');
  Serial.flush();
}

static bool readLine(String &out, uint32_t timeoutMs) {
  out = "";
  uint32_t last = millis();
  while (millis() - last < timeoutMs) {
    int c = Serial.read();
    if (c < 0) { delay(1); continue; }
    last = millis();
    if (c == '\n') return true;
    if (c == '\r') continue;
    if (out.length() < 600) out += (char)c;
  }
  return false;
}

// doc dung n byte, tra ve so byte thuc su doc duoc
static size_t readExact(uint8_t *dst, size_t n, uint32_t timeoutMs) {
  size_t got = 0;
  uint32_t last = millis();
  while (got < n && (millis() - last) < timeoutMs) {
    int avail = Serial.available();
    if (avail <= 0) { delay(0); continue; }
    size_t want = n - got;
    if ((size_t)avail < want) want = (size_t)avail;
    size_t r = Serial.readBytes(dst + got, want);
    if (r) { got += r; last = millis(); }
  }
  return got;
}

static String hex8(uint32_t v) {
  char b[9];
  snprintf(b, sizeof(b), "%08x", (unsigned)v);
  return String(b);
}

// ------------------------------------------------------------ chan doan
struct PinInfo {
  uint8_t gpio;
  const char *line;   // ten duong tin hieu
  const char *chip;   // so chan tren MKDV1GCL-ABA
  const char *xiao;   // ten chan tren XIAO
  bool needPullup;    // CLK khong can pull-up, cac duong con lai thi can
};
static const PinInfo kPins[] = {
  {PIN_CLK, "CLK ", "5", "D8 ", false},
  {PIN_CMD, "CMD ", "3", "D10", true},
  {PIN_D0,  "DAT0", "7", "D9 ", true},
  {PIN_D1,  "DAT1", "8", "D0 ", true},
  {PIN_D2,  "DAT2", "1", "D1 ", true},
  {PIN_D3,  "DAT3", "2", "D3 ", true},
};
static const size_t kNumPins = sizeof(kPins) / sizeof(kPins[0]);

// Doc muc idle cua tung duong de biet: cham mat, tha noi, hay da co pull-up.
// Pull-up ngoai 10k thang duoc pull-down noi (~45k) nen phan biet duoc.
static void diagPins() {
  Serial.println("#");
  Serial.println("# ===== CHAN DOAN DUONG DAY =====");
  Serial.println("# duong  chan-chip  chan-XIAO  gpio  ket-luan");

  bool anyLow = false, anyFloat = false;
  for (size_t i = 0; i < kNumPins; i++) {
    pinMode(kPins[i].gpio, INPUT_PULLUP);
    delayMicroseconds(500);
    bool hiPu = digitalRead(kPins[i].gpio);
    pinMode(kPins[i].gpio, INPUT_PULLDOWN);
    delayMicroseconds(500);
    bool hiPd = digitalRead(kPins[i].gpio);

    const char *verdict;
    if (!hiPu) { verdict = "CHAM MAT hoac bi giu muc thap  <-- LOI"; anyLow = true; }
    else if (hiPd) verdict = "co pull-up ngoai, tot";
    else if (kPins[i].needPullup) { verdict = "THA NOI - thieu pull-up 10k  <-- LOI"; anyFloat = true; }
    else verdict = "tha noi (CLK khong can pull-up, binh thuong)";

    Serial.printf("# %-5s  %-9s  %-9s  %-4u  %s\n",
                  kPins[i].line, kPins[i].chip, kPins[i].xiao, kPins[i].gpio, verdict);
  }

  // Cham chap giua hai duong ke nhau - loi hay gap khi han tay LGA-8.
  Serial.println("# --- kiem tra cham chap giua cac duong ---");
  bool anyBridge = false;
  for (size_t i = 0; i < kNumPins; i++) {
    for (size_t j = 0; j < kNumPins; j++) pinMode(kPins[j].gpio, INPUT_PULLUP);
    pinMode(kPins[i].gpio, OUTPUT);
    digitalWrite(kPins[i].gpio, LOW);
    delayMicroseconds(800);
    for (size_t j = 0; j < kNumPins; j++) {
      if (j == i) continue;
      if (!digitalRead(kPins[j].gpio)) {
        Serial.printf("# CHAP: %s (chan %s) dinh voi %s (chan %s)\n",
                      kPins[i].line, kPins[i].chip, kPins[j].line, kPins[j].chip);
        anyBridge = true;
      }
    }
    pinMode(kPins[i].gpio, INPUT_PULLUP);
  }
  if (!anyBridge) Serial.println("# khong thay cham chap.");
  if (anyLow) Serial.println("# (duong cham mat se bao chap voi tat ca - sua cham mat truoc)");

  Serial.println("#");
  Serial.println("# GOI Y THEO THU TU KHA NANG:");
  if (anyLow)
    Serial.println("#  1. Co duong bi giu muc thap. DAT0 muc thap la dung nguyen nhan loi"
                   " 0x107: SDMMC coi the dang ban nen treo ngay o buoc cap nhat clock.");
  if (anyFloat)
    Serial.println("#  2. Thieu pull-up 10k len 3V3. Chuan SD bat buoc co tren CMD va DAT0-DAT3.");
  if (!anyLow && !anyFloat)
    Serial.println("#  1. Duong day nhin on. Kiem tra 3V3 tai chan 4 va GND tai chan 6 cua chip,"
                   " va xem co board mo rong Sense dang cam khong (the microSD tren do"
                   " dung chung D8/D9/D10).");
  Serial.println("#  3. Do 3V3 ngay tai chan 4 cua chip, phai >= 3.0V khi dang chay.");
  Serial.println("#  4. Thu ep SPI: dat FORCE_MODE = 3 trong sketch roi nap lai.");
  Serial.println("# ===============================");

  for (size_t i = 0; i < kNumPins; i++) pinMode(kPins[i].gpio, INPUT_PULLUP);
}

// ----------------------------------------------------------------- mount
static bool probeFs() {
  if (!gfs) return false;
  File r = gfs->open("/");
  if (!r) return false;
  r.close();
  return true;
}

static bool mountSdmmc(bool oneBit, bool allowFormat) {
  SD_MMC.end();
  bool pinsOk = oneBit ? SD_MMC.setPins(PIN_CLK, PIN_CMD, PIN_D0)
                       : SD_MMC.setPins(PIN_CLK, PIN_CMD, PIN_D0, PIN_D1, PIN_D2, PIN_D3);
  if (!pinsOk) return false;
#if !AUTO_FORMAT_IF_UNMOUNTABLE
  allowFormat = false;
#endif
  if (!SD_MMC.begin("/sdcard", oneBit, allowFormat, SDMMC_KHZ, 8)) return false;
  if (SD_MMC.cardType() == CARD_NONE) { SD_MMC.end(); return false; }
  gfs = &SD_MMC;
  gMode = oneBit ? "SDIO-1bit" : "SDIO-4bit";
  return probeFs();
}

static bool mountSpiSlow();
static bool mountSpiAt(uint32_t hz, const char *label) {
  // DAT1/DAT2 khong dung trong SPI -> keo len de chip khong hieu nham
  pinMode(PIN_D1, INPUT_PULLUP);
  pinMode(PIN_D2, INPUT_PULLUP);
  SD.end();
  SPI.end();
  SPI.begin(PIN_CLK, PIN_D0, PIN_CMD, PIN_CS);   // sck, miso, mosi, ss
  if (!SD.begin(PIN_CS, SPI, hz)) return false;
  if (SD.cardType() == CARD_NONE) { SD.end(); return false; }
  gfs = &SD;
  gMode = label;
  return probeFs();
}
static bool mountSpi()     { return mountSpiAt(SPI_FREQ_HZ, "SPI-20MHz"); }
static bool mountSpiSlow() { return mountSpiAt(1000000UL,   "SPI-1MHz"); }

static bool mountAny() {
  gfs = nullptr;
  const int force = FORCE_MODE;

  if (force == 0 || force == 1) {
    Serial.println("# thu SDIO 4-bit...");
    if (mountSdmmc(false, false)) return true;
  }
  if (force == 0 || force == 2) {
    Serial.println("# thu SDIO 1-bit...");
    if (mountSdmmc(true, false)) return true;
  }
  if (force == 0 || force == 3) {
    Serial.println("# thu SPI 20MHz...");
    if (mountSpi()) return true;
    Serial.println("# thu SPI 1MHz...");
    if (mountSpiSlow()) return true;
  }
  // Het cach doc duoc -> the co the chua bao gio duoc format.
  if (force == 0 || force == 2) {
    Serial.println("# thu SDIO 1-bit kem format...");
    if (mountSdmmc(true, true)) return true;
  }
  gMode = "none";
  return false;
}

// FATFS co bat Long File Name khong? Ten kieu "frame_000.png" can LFN.
static void checkLfn() {
  gLfn = false;
  if (!gfs) return;
  const char *probe = "/lfn_probe_name.tmp";
  gfs->remove(probe);
  File f = gfs->open(probe, FILE_WRITE);
  if (!f) return;
  f.write((const uint8_t *)"x", 1);
  f.close();
  gLfn = gfs->exists(probe);
  gfs->remove(probe);
}

static uint64_t cardBytes() {
  if (gfs == &SD_MMC) return SD_MMC.cardSize();
  if (gfs == &SD)     return SD.cardSize();
  return 0;
}
static uint64_t totalBytes() {
  if (gfs == &SD_MMC) return SD_MMC.totalBytes();
  if (gfs == &SD)     return SD.totalBytes();
  return 0;
}
static uint64_t usedBytes() {
  if (gfs == &SD_MMC) return SD_MMC.usedBytes();
  if (gfs == &SD)     return SD.usedBytes();
  return 0;
}

// ----------------------------------------------------------------- file ops
static bool ensureDirs(const String &path) {
  int idx = 1;
  while (true) {
    int slash = path.indexOf('/', idx);
    if (slash < 0) break;
    String sub = path.substring(0, slash);
    if (sub.length() > 1 && !gfs->exists(sub)) {
      if (!gfs->mkdir(sub)) return false;
    }
    idx = slash + 1;
  }
  return true;
}

static bool fileCrc(const String &path, uint32_t &crcOut, uint32_t &sizeOut) {
  File f = gfs->open(path, FILE_READ);
  if (!f) return false;
  if (f.isDirectory()) { f.close(); return false; }
  uint32_t crc = crcBegin();
  uint32_t total = 0;
  while (true) {
    int n = f.read(gBuf, sizeof(gBuf));
    if (n <= 0) break;
    crc = crcUpdate(crc, gBuf, (size_t)n);
    total += (uint32_t)n;
  }
  f.close();
  crcOut = crcFinish(crc);
  sizeOut = total;
  return true;
}

static void rmRecursive(const String &path) {
  File d = gfs->open(path);
  if (!d) return;
  if (!d.isDirectory()) { d.close(); gfs->remove(path); return; }
  while (true) {
    File e = d.openNextFile();
    if (!e) break;
    String child = String(e.path());
    bool isDir = e.isDirectory();
    e.close();
    if (isDir) rmRecursive(child);
    else gfs->remove(child);
  }
  d.close();
  gfs->rmdir(path);
}

// ----------------------------------------------------------------- lenh
static void cmdInfo() {
  String s = String(gMode) + " lfn=" + (gLfn ? "1" : "0") +
             " card=" + String((unsigned long)(cardBytes() / 1024ULL)) +
             " total=" + String((unsigned long)(totalBytes() / 1024ULL)) +
             " used=" + String((unsigned long)(usedBytes() / 1024ULL)) +
             " chunk=" + String(CHUNK_SIZE) +
             " fw=" FW_VERSION;
  ok(s);
}

static void cmdStat(const String &path) {
  uint32_t crc = 0, size = 0;
  if (!gfs->exists(path)) { ok("NONE"); return; }
  if (!fileCrc(path, crc, size)) { ok("NONE"); return; }
  ok(String(size) + " " + hex8(crc));
}

static void cmdLs(const String &path) {
  File d = gfs->open(path.length() ? path : String("/"));
  if (!d) { err("notdir"); return; }
  if (!d.isDirectory()) { d.close(); err("notdir"); return; }
  while (true) {
    File e = d.openNextFile();
    if (!e) break;
    Serial.print(e.isDirectory() ? "D " : "F ");
    Serial.print((unsigned long)e.size());
    Serial.print(' ');
    Serial.print(e.name());
    Serial.print('\n');
    e.close();
  }
  d.close();
  ok("END");
}

static void cmdPut(const String &args) {
  // PUT <size> <crc32hex> <path>
  int s1 = args.indexOf(' ');
  int s2 = (s1 < 0) ? -1 : args.indexOf(' ', s1 + 1);
  if (s1 < 0 || s2 < 0) { err("syntax"); return; }
  uint32_t size = (uint32_t)strtoul(args.substring(0, s1).c_str(), nullptr, 10);
  uint32_t want = (uint32_t)strtoul(args.substring(s1 + 1, s2).c_str(), nullptr, 16);
  String path = args.substring(s2 + 1);
  path.trim();
  if (!path.startsWith("/")) path = String("/") + path;

  if (!ensureDirs(path)) { err("mkdir"); return; }
  File f = gfs->open(path, FILE_WRITE);
  if (!f) { err("open"); return; }

  ok("RDY");                       // tu day PC bat dau ban chunk nhi phan

  uint32_t crc = crcBegin();
  uint32_t left = size;
  bool bad = false;
  while (left) {
    size_t n = (left > CHUNK_SIZE) ? CHUNK_SIZE : (size_t)left;
    size_t got = readExact(gBuf, n, 15000);
    if (got != n) { err("rxtimeout"); bad = true; break; }
    if (f.write(gBuf, n) != n) { err("write"); bad = true; break; }
    crc = crcUpdate(crc, gBuf, n);
    left -= n;
    Serial.print("K\n");
    Serial.flush();
  }
  f.close();
  if (bad) { gfs->remove(path); return; }

  uint32_t got = crcFinish(crc);
  if (got != want) {
    gfs->remove(path);
    err(String("crc ") + hex8(got) + "!=" + hex8(want));
    return;
  }
  ok(String("DONE ") + hex8(got));
}

static void cmdFormat() {
  bool onebit = (String(gMode) == "SDIO-1bit");
  SD_MMC.end();
  SD.end();
  gfs = nullptr;
  SD_MMC.setPins(PIN_CLK, PIN_CMD, PIN_D0, PIN_D1, PIN_D2, PIN_D3);
  if (SD_MMC.begin("/sdcard", onebit, true, SDMMC_KHZ, 8)) {
    SD_MMC.end();
  }
  if (mountAny()) { checkLfn(); ok(String("FORMATTED ") + gMode); }
  else err("format");
}

static void dispatch(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = (sp < 0) ? line : line.substring(0, sp);
  String arg = (sp < 0) ? String() : line.substring(sp + 1);
  cmd.toUpperCase();
  arg.trim();
  if (cmd != "PUT" && arg.length() && !arg.startsWith("/")) arg = String("/") + arg;

  if (cmd == "PING")    { ok("PONG " FW_VERSION); return; }
  if (cmd == "DIAG")    { diagPins(); ok("DIAG END"); return; }
  if (cmd == "REMOUNT") { if (mountAny()) { checkLfn(); cmdInfo(); } else err("mount"); return; }

  if (!gfs) { err("nomount"); return; }

  if      (cmd == "INFO")   { cmdInfo(); }
  else if (cmd == "STAT")   { cmdStat(arg); }
  else if (cmd == "MKDIR")  { if (ensureDirs(arg + "/")) ok(); else err("mkdir"); }
  else if (cmd == "PUT")    { cmdPut(arg); }
  else if (cmd == "LS")     { cmdLs(arg); }
  else if (cmd == "RM")     { gfs->remove(arg); ok(); }
  else if (cmd == "RMDIR")  { rmRecursive(arg); ok(); }
  else if (cmd == "FORMAT") { cmdFormat(); }
  else if (cmd == "DONE")   { ok("BYE"); }
  else                      { err(String("unknown ") + cmd); }
}

// ----------------------------------------------------------------- setup/loop
void setup() {
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);   // XIAO: LED tat khi muc HIGH
#endif
  crcInit();
  Serial.setRxBufferSize(RX_BUF_SIZE);
  Serial.begin(921600);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);
  delay(300);

  Serial.println();
  Serial.println("# ESP32-S3 <-> MKDV1GCL-ABA SD NAND uploader v" FW_VERSION);

  if (mountAny()) {
    checkLfn();
    Serial.printf("# mounted: %s  card=%llu MB  total=%llu MB  used=%llu MB  lfn=%d\n",
                  gMode,
                  cardBytes() / (1024ULL * 1024ULL),
                  totalBytes() / (1024ULL * 1024ULL),
                  usedBytes() / (1024ULL * 1024ULL),
                  gLfn ? 1 : 0);
    if (!gLfn) Serial.println("# CANH BAO: FATFS khong bat LFN, ten file dai se bi cat!");
#ifdef LED_BUILTIN
    digitalWrite(LED_BUILTIN, LOW);
#endif
  } else {
    Serial.println("# LOI: khong mount duoc SD NAND.");
    diagPins();          // tu chan doan ngay, khoi phai go loi mo
  }

  // Tu day tro di kenh serial la kenh giao thuc: log cua ESP-IDF (nhung dong
  // "E (1234) sdmmc_periph: ...") se lam hong phan hoi OK/ER nen phai tat.
  esp_log_level_set("*", ESP_LOG_NONE);

  Serial.println("# san sang, cho lenh tu PC...");
}

void loop() {
  String line;
  if (readLine(line, 1000)) dispatch(line);
}
