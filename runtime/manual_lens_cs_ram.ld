ENTRY(nx_cs_ram_update)
SECTIONS {
  . = 0;
  .header : {
    LONG(0x4e584353) LONG(1) LONG(__blob_end) LONG(__data_start)
    LONG(__data_start) LONG(__blob_end - __data_start)
    LONG(nx_cs_ram_update) LONG(nx_cs_ram_common)
    LONG(nx_cs_ram_info) LONG(nx_cs_ram_onoff)
    LONG(nx_cs_ram_config) LONG(200)
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
