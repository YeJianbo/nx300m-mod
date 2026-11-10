"""Focused settings checks using the existing private ARM menu fixture."""
import sys
from pathlib import Path

import probe_fw_controls_vm as fw

ROOT = Path(__file__).resolve().parents[1]
EXTRA = r'''
# The menu runs in RAM, but saved preferences live on the source card.
mkdir -p /perf/source/.state
export NX300M_MOD_SOURCE_ROOT=/perf/source
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/source)
mv /var/run/nx300m-firmware-mod-ready /perf/fw-ready
mv /var/run/nx300m-exposure-ready /perf/iso-ready
NX_STATE_DIR=/tmp/offline-settings /rescue/busybox timeout 45 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/offline.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/offline.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
for k in KP_Right KP_Right KP_Right KP_Right KP_Enter KP_Enter; do key "$k"; done
[ "$(cat /perf/source/.state/fw-mode-lock)" = 1 ]
key KP_Down; key KP_Enter
[ "$(cat /perf/source/.state/fw-animation)" = 1 ]
key KP_Down; key KP_Down; key KP_Enter
[ "$(cat /perf/source/.state/fw-auto-iso-lock)" = 6400 ]
[ "$(cat /perf/source/.state/fw-auto-iso-max)" = 6400 ]
key Menu
key KP_Right; key KP_Right; key KP_Enter
key KP_Down; key KP_Down; key KP_Down; key KP_Enter
[ "$(cat /perf/source/.state/fw-auto-iso-max)" = 12800 ]
[ "$(cat /perf/source/.state/fw-auto-iso-lock)" = 0 ]
! grep -q '^BACKEND kind=5' /perf/offline.log
key Menu; key Menu
wait "$panel"
echo OFFLINE_SETTINGS_SAVED_NOT_APPLIED
mv /perf/fw-ready /var/run/nx300m-firmware-mod-ready
mv /perf/iso-ready /var/run/nx300m-exposure-ready
printf '0\n' > /perf/mod/.state/fw-mode-lock
printf '25600\n' > /perf/source/.state/fw-auto-iso-max
printf '6400\n' > /perf/source/.state/fw-auto-iso-lock
mkdir -p /tmp/settings-restore
NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/settings-restore /bin/sh /perf/mod/firmware-mod-restore.sh
[ "$(awk '{print $14}' /var/run/nx300m-firmware-mod-response)" = 1 ]
[ "$(awk '{print $17}' /var/run/nx300m-firmware-mod-response)" = 0 ]
[ "$(awk '{print $4}' /var/run/nx300m-exposure-response)" = 003b0011 ]
grep -q 'restored fw-mode-lock=1' /perf/source/.state/fw-restore.log
grep -q 'restored fw-auto-iso-max=25600' /perf/source/.state/fw-restore.log
echo SOURCE_CARD_RESTORE_AND_ISO_CONFLICT_OK
'''


if __name__ == '__main__':
    fw.PROBE = fw.PROBE.replace('kill "$bridge"\nkill "$stock"', EXTRA+'\nkill "$bridge"\nkill "$stock"')
    output=ROOT/'outputs/controls/settings-extension-vm-final'
    sys.argv = [__file__, '--auto-iso-only', '--output-dir',str(output)]
    fw.main()
    import json
    report=json.loads((output/'report.json').read_text(encoding='utf-8'))
    execution=json.loads((output/'execution.json').read_text(encoding='utf-8'))
    report['offline_settings_saved_not_applied']='OFFLINE_SETTINGS_SAVED_NOT_APPLIED' in execution['output']
    report['source_card_restore_and_iso_conflict']='SOURCE_CARD_RESTORE_AND_ISO_CONFLICT_OK' in execution['output']
    report['passed']=report['passed'] and report['offline_settings_saved_not_applied'] and report['source_card_restore_and_iso_conflict']
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
