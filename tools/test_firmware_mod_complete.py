"""Read-only firmware bridge build inspection, not a full camera function test.

Kept as a compatibility command. Never writes IPC files into the stock rootfs.
Actual isolated runtime results are recorded separately by the runtime probe.
"""
from probe_camera_firmware_mod import main

if __name__ == '__main__':
    main()
