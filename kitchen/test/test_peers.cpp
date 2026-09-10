// =============================================================================
// test_peers.cpp — PeerAlarmTable, the primary lab-wide interlock.
//
// These guard the bug the table replaced: a flat "any peer alarming" boolean
// let one zone's "clear" cancel another zone's active alarm. Zones publish
// independently on retained topics, so per-zone rows are the whole point.
//
// Also pinned here: silence NEVER clears an active zone (staleness is a warn),
// and table overflow FAILS SAFE rather than dropping an alarming zone.
// =============================================================================

#include "test_harness.h"

static const char* ZONE_A = "status/ExpA/DEV-A/alarm/lab5/hydrogen";
static const char* ZONE_B = "status/ExpB/DEV-B/alarm/lab5/hydrogen";

TEST(peer_table_starts_clear) {
  PeerAlarmTable t;
  CHECK(t.anyActive() == false);
  CHECK(t.everSeen() == false);
  CHECK(t.trackedZones() == 0);
}

TEST(peer_single_zone_active_then_cleared) {
  PeerAlarmTable t;
  t.update(ZONE_A, true, 1000);
  CHECK(t.anyActive() == true);
  t.update(ZONE_A, false, 2000);
  CHECK(t.anyActive() == false);
}

// THE REGRESSION: zone B clearing must not release zone A's alarm.
TEST(peer_one_zone_clearing_does_not_cancel_another) {
  PeerAlarmTable t;
  t.update(ZONE_A, true, 1000);     // A alarms
  t.update(ZONE_B, true, 1100);     // B alarms too
  CHECK(t.trackedZones() == 2);

  t.update(ZONE_B, false, 2000);    // B stands down
  CHECK(t.anyActive() == true);     // ...A is still alarming

  t.update(ZONE_A, false, 3000);    // only now, with A clear too
  CHECK(t.anyActive() == false);
}

TEST(peer_unknown_zone_clear_claims_no_row) {
  PeerAlarmTable t;
  t.update(ZONE_A, false, 1000);    // never-seen zone reporting clear
  CHECK(t.trackedZones() == 0);     // no row wasted
  CHECK(t.anyActive() == false);
}

TEST(peer_silence_never_clears_an_active_zone) {
  PeerAlarmTable t;
  t.update(ZONE_A, true, 1000);
  // A goes silent for a long time — staleness warns, but the interlock holds.
  CHECK(t.anyStale(1000 + 60000, 30000) == true);
  CHECK(t.anyActive() == true);
}

TEST(peer_staleness_is_per_zone_and_warn_only) {
  PeerAlarmTable t;
  // Claim rows (an alarm claims a row; a clear from an unseen zone does not),
  // then stand both down so the table is tracking two quiet-but-known zones.
  t.update(ZONE_A, true, 500);
  t.update(ZONE_B, true, 500);
  t.update(ZONE_A, false, 1000);
  t.update(ZONE_B, false, 1000);

  CHECK(t.anyStale(1000 + 29999, 30000) == false);
  CHECK(t.anyStale(1000 + 30000, 30000) == true);
  CHECK(t.anyActive() == false);    // stale != active
}

TEST(peer_repeat_message_updates_same_row) {
  PeerAlarmTable t;
  for (int i = 0; i < 100; i++) t.update(ZONE_A, true, 1000 + i);
  CHECK(t.trackedZones() == 1);     // no row leak on republish
}

TEST(peer_table_overflow_fails_safe) {
  PeerAlarmTable t;
  char topic[80];
  for (int i = 0; i < KITCHEN_MAX_PEER_ZONES; i++) {
    snprintf(topic, sizeof(topic), "status/E/DEV-%d/alarm/lab5/hydrogen", i);
    t.update(topic, true, 1000);
  }
  CHECK(t.trackedZones() == KITCHEN_MAX_PEER_ZONES);
  CHECK(t.overflowed() == false);

  // One zone too many, and it is ALARMING — must not be dropped.
  bool tracked = t.update("status/E/DEV-OVERFLOW/alarm/lab5/hydrogen", true, 1100);
  CHECK(tracked == false);
  CHECK(t.overflowed() == true);
  CHECK(t.anyActive() == true);

  // Even after every tracked zone stands down, the untracked one keeps it held.
  for (int i = 0; i < KITCHEN_MAX_PEER_ZONES; i++) {
    snprintf(topic, sizeof(topic), "status/E/DEV-%d/alarm/lab5/hydrogen", i);
    t.update(topic, false, 2000);
  }
  CHECK(t.anyActive() == true);
}

TEST(peer_reset_clears_everything) {
  PeerAlarmTable t;
  t.update(ZONE_A, true, 1000);
  t.reset();
  CHECK(t.anyActive() == false);
  CHECK(t.trackedZones() == 0);
  CHECK(t.everSeen() == false);
}
