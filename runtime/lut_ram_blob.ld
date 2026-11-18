ENTRY(nx_lut_ram_update)
SECTIONS {
  . = 0;
  .header : {
    LONG(0x4e584c42) LONG(1) LONG(__blob_end) LONG(__data_start)
    LONG(__data_start) LONG(__blob_end - __data_start)
    LONG(nx_lut_ram_update) LONG(nx_lut_ram_ipcs)
    LONG(nx_lut_ram_config) LONG(196)
    /* Context stride is loader-visible (header reserved[1]); the build guard
     * in build_lut_ram_blob.py fails when sizeof(struct context) drifts.
     * 9120 = 280 (tid/state/path) + tables[2][2190]*2 + 36 + 28 + stats 16. */
    LONG(nx_lut_ram_contexts) LONG(9120) LONG(8)
    LONG(nx_lut_build_tag) LONG(0) LONG(0)
  }
  .text : { *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  . = ALIGN(4096);
  __data_start = .;
  .data : { *(.data .data.*) *(.got .got.*) }
  .bss : { *(.bss .bss.*) *(COMMON) }
  . = ALIGN(4096);
  __blob_end = .;
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}
