// =============================================================================
// test_main.cpp — owns the shared counters and main(). Link this with any set
// of test_*.cpp area files; their TEST() blocks self-register at static-init
// time and have all already run by the time main() body executes, so main()
// only prints the tally and sets the exit code.
//
// This file deliberately contains NO tests, so that linking it alone is a
// valid (empty, passing) run and adding an area file is the only thing that
// adds checks.
// =============================================================================

#include <cstdio>

int g_failures = 0;
int g_total    = 0;

int main() {
  printf("\n%d/%d checks passed\n", g_total - g_failures, g_total);
  if (g_failures > 0) {
    printf("%d FAILURES\n", g_failures);
    return 1;
  }
  printf("ALL PASS\n");
  return 0;
}
