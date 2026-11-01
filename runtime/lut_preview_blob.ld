ENTRY(nx_preview_execute)
SECTIONS {
  . = 0;
  .header : {
    LONG(0x4e585056) LONG(1) LONG(__blob_end) LONG(__data_start)
    LONG(__data_start) LONG(__blob_end - __data_start)
    LONG(nx_preview_execute) LONG(nx_preview_config) LONG(16)
    LONG(nx_preview_stats) LONG(76) LONG(nx_preview_build)
    LONG(0) LONG(0) LONG(0) LONG(0)
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
