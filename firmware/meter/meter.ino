/*
  VERDET meter firmware (Arduino Mega 2560), Phase 4.

  Measures DC voltage (divider on A0) and current (ACS712 on A1), adds up
  energy in 60-second windows, signs each window with the meter's Ed25519 key,
  and prints it as one JSON line for gateway/serial_bridge.py.

  - Sampling runs in a 1 kHz timer interrupt (500 reads/s per channel), so it
    keeps going while the main loop is busy signing (about 6.3 s per window).
  - The key and the sequence number live in EEPROM (same layout as the spike),
    so the public key and seq survive power cycles.
  - The signed text is the canonical string (CLAUDE.md rule 4). It must match
    gateway/canonical.py byte for byte:
      VERDET|v1|{meterId}|{seq}|{tStartMs}|{tEndMs}|{mWh}|{peakMw}|{avgMv}|{avgMa}
  - Lines starting with '#' are debug output; the bridge only forwards '{' lines.

  Library: "Crypto" by Rhys Weatherley (Library Manager).
  Serial: 115200 baud.
*/

#include <Crypto.h>
#include <Ed25519.h>
#include <RNG.h>
#include <EEPROM.h>
#include <string.h>

// ---------------- Identity -------------------------------------------------
// Must match the meter ID you register on /meters (letters, digits, dashes).
#define METER_ID "VD-0001"

// ---------------- Calibration (edit after the multimeter check) -------------
const float VREF_MV       = 5000.0;  // the Mega's 5V rail, measured with a multimeter
const float DIVIDER_RATIO = 5.7;     // (47k + 10k) / 10k
const float V_CAL         = 1.000;   // fine-tune: multimeter volts / meter volts
const float ACS_ZERO_MV   = 2500.0;  // ACS712 output at 0 A (read '#' lines with the load off)
const float ACS_MV_PER_A  = 100.0;   // ACS712-20A: 100 mV per amp (5A version: 185)
const float I_CAL         = 1.000;   // fine-tune: multimeter amps / meter amps
const float NOISE_FLOOR_MA = 30.0;   // below this, current counts as zero (sensor noise)

const uint8_t PIN_V = A0;
const uint8_t PIN_I = A1;
const uint16_t WINDOW_S = 60;        // seconds per signed reading

// ---------------- EEPROM layout (same as firmware/spike) -------------------
//   0..3   magic "VDK1"   4..35  Ed25519 private key   36..39  last used seq
const uint8_t MAGIC[4] = {'V', 'D', 'K', '1'};
const int ADDR_MAGIC = 0;
const int ADDR_KEY = 4;
const int ADDR_SEQ = 36;
const uint32_t NO_SEQ = 0xFFFFFFFFUL;  // fresh EEPROM: no reading sent yet

uint8_t privKey[32];
uint8_t pubKey[32];
uint8_t sig[64];

// ---------------- Measurement state (written by the timer interrupt) --------
struct Window {
  uint32_t tStartMs;
  uint32_t tEndMs;
  uint32_t energyMws;   // sum of 1-second power samples = mW·s
  uint32_t peakMw;
  uint32_t sumMv;
  uint32_t sumMa;
  uint16_t seconds;
};

Window cur;                    // the window being measured (ISR only)
Window ready;                  // the last finished window (ISR writes, loop reads)
volatile bool readyFlag = false;

uint16_t tick = 0;             // 0..999 within the current second (ISR only)
uint32_t secSumV = 0, secSumI = 0;
uint16_t secCountV = 0, secCountI = 0;

volatile uint32_t lastMv = 0, lastMa = 0, lastMw = 0;   // for '#' debug lines
volatile uint16_t lastRawV = 0, lastRawI = 0;

// ---------------- Key and seq -----------------------------------------------
bool hasKey() {
  for (int i = 0; i < 4; i++) {
    if (EEPROM.read(ADDR_MAGIC + i) != MAGIC[i]) return false;
  }
  return true;
}

// Prototype-grade entropy (same as the spike). Production: secure element.
void seedRng() {
  RNG.begin("VERDET meter v1");
  for (int round = 0; round < 64; round++) {
    uint8_t buf[16];
    for (int i = 0; i < 16; i++) {
      buf[i] = (uint8_t)(analogRead(A2 + (i % 6)) ^ (micros() & 0xFF));
      delayMicroseconds(37);
    }
    RNG.stir(buf, sizeof(buf), 4);
  }
}

void loadOrCreateKey() {
  if (hasKey()) {
    for (int i = 0; i < 32; i++) privKey[i] = EEPROM.read(ADDR_KEY + i);
    Serial.println(F("# KEY:LOADED"));
  } else {
    seedRng();
    Ed25519::generatePrivateKey(privKey);
    for (int i = 0; i < 32; i++) EEPROM.update(ADDR_KEY + i, privKey[i]);
    for (int i = 0; i < 4; i++) EEPROM.update(ADDR_MAGIC + i, MAGIC[i]);
    Serial.println(F("# KEY:NEW"));
  }
  Ed25519::derivePublicKey(pubKey, privKey);   // about 6 s on a Mega
}

// Take the next seq and save it BEFORE sending (like the simulator):
// a reading lost on the way never lets a seq be reused.
uint32_t takeNextSeq() {
  uint32_t last;
  EEPROM.get(ADDR_SEQ, last);
  uint32_t next = (last == NO_SEQ) ? 0 : last + 1;
  EEPROM.put(ADDR_SEQ, next);
  return next;
}

// ---------------- Sampling: 1 kHz timer interrupt ----------------------------
void startSampling() {
  cur = Window();
  cur.tStartMs = millis();
  noInterrupts();
  TCCR1A = 0;
  TCCR1B = (1 << WGM12) | (1 << CS11) | (1 << CS10);  // CTC mode, prescaler 64
  OCR1A = 249;                                         // 16 MHz / 64 / 250 = 1000 Hz
  TIMSK1 |= (1 << OCIE1A);
  interrupts();
}

// Called once per second from the ISR: turn 500 raw reads per channel into
// one voltage/current/power sample and add it to the window.
void finishSecond() {
  float rawV = secCountV ? (float)secSumV / secCountV : 0;
  float rawI = secCountI ? (float)secSumI / secCountI : 0;
  float mv = rawV * VREF_MV / 1023.0 * DIVIDER_RATIO * V_CAL;
  float ma = (rawI * VREF_MV / 1023.0 - ACS_ZERO_MV) * 1000.0 / ACS_MV_PER_A * I_CAL;
  if (mv < 0) mv = 0;
  if (ma < NOISE_FLOOR_MA) ma = 0;              // also drops negative readings
  uint32_t vMv = (uint32_t)mv;
  uint32_t iMa = (uint32_t)ma;
  uint32_t pMw = (uint32_t)((float)vMv * iMa / 1000.0);

  cur.energyMws += pMw;
  if (pMw > cur.peakMw) cur.peakMw = pMw;
  cur.sumMv += vMv;
  cur.sumMa += iMa;
  cur.seconds++;

  lastMv = vMv; lastMa = iMa; lastMw = pMw;
  lastRawV = (uint16_t)rawV; lastRawI = (uint16_t)rawI;
  secSumV = secSumI = 0;
  secCountV = secCountI = 0;

  if (cur.seconds >= WINDOW_S) {
    // The window length is defined by the timer: exactly WINDOW_S seconds.
    cur.tEndMs = cur.tStartMs + (uint32_t)WINDOW_S * 1000UL;
    ready = cur;
    readyFlag = true;
    uint32_t nextStart = cur.tEndMs;
    cur = Window();
    cur.tStartMs = nextStart;                   // windows are back to back, no gaps
  }
}

ISR(TIMER1_COMPA_vect) {
  if (tick & 1) { secSumI += analogRead(PIN_I); secCountI++; }
  else          { secSumV += analogRead(PIN_V); secCountV++; }
  if (++tick >= 1000) {
    tick = 0;
    finishSecond();
  }
}

// ---------------- Output ------------------------------------------------------
void printHex(const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    if (data[i] < 0x10) Serial.print('0');
    Serial.print(data[i], HEX);
  }
}

void sendReading(const Window &w) {
  uint32_t seq = takeNextSeq();
  uint32_t mWh = w.energyMws / 3600UL;
  uint32_t avgMv = w.seconds ? w.sumMv / w.seconds : 0;
  uint32_t avgMa = w.seconds ? w.sumMa / w.seconds : 0;

  char msg[128];
  snprintf(msg, sizeof(msg), "VERDET|v1|%s|%lu|%lu|%lu|%lu|%lu|%lu|%lu",
           METER_ID, (unsigned long)seq, (unsigned long)w.tStartMs, (unsigned long)w.tEndMs,
           (unsigned long)mWh, (unsigned long)w.peakMw, (unsigned long)avgMv, (unsigned long)avgMa);

  Ed25519::sign(sig, privKey, pubKey, msg, strlen(msg));   // about 6.3 s; sampling continues

  Serial.print(F("{\"meterId\":\"")); Serial.print(F(METER_ID));
  Serial.print(F("\",\"seq\":"));     Serial.print((unsigned long)seq);
  Serial.print(F(",\"tStartMs\":"));  Serial.print((unsigned long)w.tStartMs);
  Serial.print(F(",\"tEndMs\":"));    Serial.print((unsigned long)w.tEndMs);
  Serial.print(F(",\"mWh\":"));       Serial.print((unsigned long)mWh);
  Serial.print(F(",\"peakMw\":"));    Serial.print((unsigned long)w.peakMw);
  Serial.print(F(",\"avgMv\":"));     Serial.print((unsigned long)avgMv);
  Serial.print(F(",\"avgMa\":"));     Serial.print((unsigned long)avgMa);
  Serial.print(F(",\"sig\":\""));     printHex(sig, 64);
  Serial.println(F("\"}"));
}

void printLive() {
  noInterrupts();
  uint32_t mv = lastMv, ma = lastMa, mw = lastMw;
  uint16_t rv = lastRawV, ri = lastRawI;
  interrupts();
  Serial.print(F("# live V=")); Serial.print(mv); Serial.print(F(" mV  I="));
  Serial.print(ma); Serial.print(F(" mA  P=")); Serial.print(mw);
  Serial.print(F(" mW  rawV=")); Serial.print(rv); Serial.print(F(" rawI=")); Serial.println(ri);
}

// ---------------- Main --------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.println(F("# VERDET meter " METER_ID " starting"));
  loadOrCreateKey();
  Serial.print(F("# PUB:")); printHex(pubKey, 32); Serial.println();
  uint32_t last;
  EEPROM.get(ADDR_SEQ, last);
  Serial.print(F("# LAST_SEQ:"));
  if (last == NO_SEQ) Serial.println(F("none")); else Serial.println((unsigned long)last);
  startSampling();
  Serial.println(F("# measuring; one signed reading every 60 s"));
}

unsigned long lastLiveMs = 0;

void loop() {
  if (readyFlag) {
    Window w;
    noInterrupts();
    memcpy(&w, &ready, sizeof(w));
    readyFlag = false;
    interrupts();
    sendReading(w);
  }
  if (millis() - lastLiveMs >= 5000) {
    lastLiveMs = millis();
    printLive();
  }
}
