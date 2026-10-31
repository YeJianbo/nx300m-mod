/* Exercise malformed device records without changing camera/sysfs state. */
#include <stdio.h>
#include <ctype.h>
#include <string.h>
static const unsigned char *fixture;
static size_t fixture_size;
static FILE *fixture_open(const char *path,const char *mode) {
    (void)path;(void)mode;
    if (!fixture) return NULL;
    FILE *f=tmpfile();if (!f) return NULL;
    if (fwrite(fixture,1,fixture_size,f)!=fixture_size) {fclose(f);return NULL;}
    rewind(f);return f;
}
static int is_old_nx_model(void) {return 1;}
enum {MODE_UNKNOWN=0};
static void set_dial_mode(int mode) {(void)mode;}
static const char *get_dial_mode_string(void) {return "UNKNOWN";}
static int get_battery_level(void) {return -1;}
#define fopen fixture_open
#include "../mod/metol/remote/status_values.h"
#undef fopen
#define CHECK(x) do {if (!(x)) {printf("failed line %d\n",__LINE__);return 1;}} while(0)
int main(void) {
    const char *bad[]={"", "bad", "6junk", "66", "-1", "6\0junk"};
    for (unsigned i=0;i<sizeof bad/sizeof *bad;i++) {
        fixture=(const unsigned char *)bad[i];fixture_size=i==5?6:strlen(bad[i]);
        CHECK(!strcmp(camera_mode_string(),"UNKNOWN"));
    }
    fixture=(const unsigned char *)" \t6\n";fixture_size=4;CHECK(!strcmp(camera_mode_string(),"P"));
    fixture=(const unsigned char *)"0";fixture_size=1;CHECK(!strcmp(camera_mode_string(),"Lens Priority"));
    CHECK(!strcmp(camera_charging_json(),"false"));
    fixture=(const unsigned char *)"1\n";fixture_size=2;CHECK(!strcmp(camera_charging_json(),"true"));
    fixture=(const unsigned char *)"2";fixture_size=1;CHECK(!strcmp(camera_charging_json(),"null"));
    unsigned char capacity[17]={0};fixture=capacity;fixture_size=17;
    capacity[16]='3';CHECK(camera_battery_level()==3);
    capacity[16]=0xff;CHECK(camera_battery_level()==-1);
    fixture_size=16;CHECK(camera_battery_level()==-1);
    fixture=NULL;CHECK(camera_battery_level()==-1 && !strcmp(camera_mode_string(),"UNKNOWN"));
    puts("status malformed/empty/NUL inputs rejected; valid dial/charge/capacity preserved");return 0;
}
