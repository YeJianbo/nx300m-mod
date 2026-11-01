ENTRY(nx_sfr_preview)
SECTIONS {
  . = 0;
  .header : {
    LONG(0x5246534e) LONG(1) LONG(__blob_end) LONG(__data_start)
    LONG(__data_start) LONG(__blob_end - __data_start)
    LONG(nx_sfr_preview) LONG(nx_sfr_effect) LONG(nx_sfr_prepare)
    LONG(nx_sfr_finish) LONG(nx_sfr_still_mode)
    LONG(nx_sfr_config) LONG(SIZEOF(.config))
    LONG(nx_sfr_runtime) LONG(SIZEOF(.runtime)) LONG(nx_sfr_build)
  }
  .text : { *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  . = ALIGN(4096);
  __data_start = .;
  .data : { *(.data .data.*) *(.got .got.*) }
  .config : { *(.config) }
  .runtime : { *(.runtime) }
  .bss : { *(.bss .bss.*) *(COMMON) }
  . = ALIGN(4096);
  __blob_end = .;
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}
