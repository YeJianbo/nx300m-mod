ENTRY(nx_ipcm_ccm)
SECTIONS {
  . = 0;
  .header : {
    LONG(0x4950584e) LONG(3) LONG(__blob_end) LONG(__data_start)
    LONG(__data_start) LONG(__blob_end - __data_start)
    LONG(nx_ipcm_ccm) LONG(nx_ipcm_tone) LONG(nx_ipcm_chm)
    LONG(nx_ipcm_pcse) LONG(nx_ipcm_color_update) LONG(nx_ipcm_gamma_update) LONG(nx_ipcm_pcse_update)
    LONG(nx_ipcm_config) LONG(nx_ipcm_config_size)
    LONG(nx_ipcm_stats) LONG(nx_ipcm_stats_size) LONG(nx_ipcm_build)
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
