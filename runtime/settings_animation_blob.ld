ENTRY(nx_animation_draw)
SECTIONS {
  . = 0;
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
