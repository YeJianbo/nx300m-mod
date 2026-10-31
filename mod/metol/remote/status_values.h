/* NX300M uses a one-digit sysfs dial enum, unlike the newer model API. */
static int camera_status_digit(const unsigned char *value,size_t count) {
    size_t index=0;
    while (index<count && isspace(value[index])) index++;
    if (index==count || value[index]<'0' || value[index]>'9') return -1;
    int result=value[index++]-'0';
    while (index<count && isspace(value[index])) index++;
    return index==count?result:-1;
}
static int camera_read_digit(const char *path) {
    FILE *file=fopen(path,"r");if (!file) return -1;
    unsigned char value[64];size_t count=fread(value,1,sizeof value,file);
    int failed=ferror(file);if (fclose(file)) failed=1;
    return failed || !count || count==sizeof value?-1:camera_status_digit(value,count);
}
static const char *camera_mode_string(void) {
    if (!is_old_nx_model()) {set_dial_mode(MODE_UNKNOWN);return get_dial_mode_string();}
    switch (camera_read_digit("/sys/devices/platform/d4keys-polled/mode")) {
        case 0:return "Lens Priority";
        case 2:return "Wi-Fi";
        case 3:return "Smart";
        case 5:return "Smart Auto";
        case 6:return "P";
        case 7:return "A";
        case 8:return "S";
        case 9:return "M";
        default:return "UNKNOWN";
    }
}
static int camera_battery_level(void) {
    if (!is_old_nx_model()) return get_battery_level();
    FILE *file=fopen("/var/run/memory/Battery/Capacity","rb");if (!file) return -1;
    unsigned char value[17];size_t count=fread(value,1,sizeof value,file);
    int failed=ferror(file);if (fclose(file)) failed=1;
    return failed || count!=sizeof value || value[16]<'0' || value[16]>'9'?-1:value[16]-'0';
}
static const char *camera_charging_json(void) {
    int value=camera_read_digit("/sys/devices/platform/jack/charger1_online");
    return value==0?"false":value==1?"true":"null";
}
