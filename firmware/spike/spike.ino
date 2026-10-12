/*
  VERDET — Phase 0 signing spike (Arduino Mega 2560)
  Save as: firmware/spike/spike.ino

  What it does:
    1. First boot: generates an Ed25519 key and stores it in EEPROM.
       Later boots: loads the same key, so PUB should never change.
    2. Prints the public key, signs a test reading string, and prints the signature.
    3. Prints how long signing took and how much RAM is free.
    The private key is NEVER printed.

  Library: Arduino IDE → Tools → Manage Libraries → search "Crypto"
           → install "Crypto" by Rhys Weatherley.

  Serial: 115200 baud. Close the Serial Monitor before running
          gateway/verify_test.py (only one program can hold the port).
*/

#include <Crypto.h>
#include <Ed25519.h>
#include <RNG.h>
#include <EEPROM.h>
#include <string.h>

// EEPROM layout (start of EEPROM; the RNG library may use the END of EEPROM):
//   0..3   magic "VDK1"  (marks that a key exists)
//   4..35  Ed25519 private key (32 bytes)
//   36..39 reserved for the sequence number (Phase 4)
const uint8_t MAGIC[4] = {'V', 'D', 'K', '1'};
const int ADDR_MAGIC = 0;
const int ADDR_KEY = 4;

// Same shape as the real canonical string (CLAUDE.md rule 4).
const char TEST_MSG[] = "VERDET|v1|VD-0001|1|0|60000|167|10120|18000|560";

uint8_t privKey[32];
uint8_t pubKey[32];
uint8_t sig[64];

extern int __heap_start, *__brkval;
int freeRam() {
  int v;
  return (int)&v - (__brkval == 0 ? (int)&__heap_start : (int)__brkval);
}

void printHex(const char *label, const uint8_t *data, size_t len) {
  Serial.print(label);
  for (size_t i = 0; i < len; i++) {
    if (data[i] < 0x10) Serial.print('0');
    Serial.print(data[i], HEX);
  }
  Serial.println();
}

bool hasKey() {
  for (int i = 0; i < 4; i++) {
    if (EEPROM.read(ADDR_MAGIC + i) != MAGIC[i]) return false;
  }
  return true;
}

// Prototype-grade entropy: noise from floating analog pins + timing jitter.
// Production would use a secure element (ATECC608) — see the threat model.
void seedRng() {
  RNG.begin("VERDET meter v1");
  for (int round = 0; round < 64; round++) {
    uint8_t buf[16];
    for (int i = 0; i < 16; i++) {
      buf[i] = (uint8_t)(analogRead(A0 + (i % 8)) ^ (micros() & 0xFF));
      delayMicroseconds(37);
    }
    RNG.stir(buf, sizeof(buf), 4);
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {}
  Serial.println(F("VERDET signing spike"));

  if (hasKey()) {
    for (int i = 0; i < 32; i++) privKey[i] = EEPROM.read(ADDR_KEY + i);
    Serial.println(F("KEY:LOADED"));
  } else {
    seedRng();
    Ed25519::generatePrivateKey(privKey);
    for (int i = 0; i < 32; i++) EEPROM.update(ADDR_KEY + i, privKey[i]);
    for (int i = 0; i < 4; i++) EEPROM.update(ADDR_MAGIC + i, MAGIC[i]);
    Serial.println(F("KEY:NEW"));
  }

  Ed25519::derivePublicKey(pubKey, privKey);
  printHex("PUB:", pubKey, 32);

  Serial.print(F("MSG:"));
  Serial.println(TEST_MSG);

  unsigned long t0 = millis();
  Ed25519::sign(sig, privKey, pubKey, TEST_MSG, strlen(TEST_MSG));
  unsigned long signMs = millis() - t0;
  printHex("SIG:", sig, 64);

  bool ok = Ed25519::verify(sig, pubKey, TEST_MSG, strlen(TEST_MSG));

  Serial.print(F("SIGN_MS:"));
  Serial.println(signMs);
  Serial.print(F("FREE_RAM:"));
  Serial.println(freeRam());
  Serial.print(F("SELF_VERIFY:"));
  Serial.println(ok ? F("OK") : F("FAIL"));
  Serial.println(F("DONE"));
}

void loop() {}
