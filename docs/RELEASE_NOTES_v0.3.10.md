# Sonix 0.3.10

Rebuilt on the mpc-vst-plugins wrapper of 2026-10-21: it no longer asks MPC to rebuild its parameter screen on every
parameter write, which left the physical Q-Links unable to move a parameter (or snapping it back) on track edit →
automation. Sonix also stops rebuilding the screen on its own display updates (`DISPLAY_REV_NO_UPDATE`): its LCD text still
refreshes through the wrapper's 100 ms poll, and the pages, theme and LEDs follow through value reports.

Check on the device:
- **track edit → automation, turn the Q-Links**: each moves its parameter and stays;
- on the plugin screen: theme arrows (the whole page reskins), BROWSE / LAYERS view, bank tabs and favourites, preset
  stepping, the LCD lines while turning knobs, the MIDI and LFO lights, the CPU meter.

`vst/params.lock` (written by gen_vst.py) is committed: the build now fails if a parameter changes index. Release zips are
built by GitHub Actions (Actions → Release → Run workflow), taking the packed instruments from the 0.3.9 release.
