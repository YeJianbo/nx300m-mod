"""Build the NX300M native remote backend and adapt the pinned Metol web client."""
from pathlib import Path
import argparse
import shlex
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def change(path, before, after):
    text = path.read_text(encoding='utf-8')
    if text.count(before) != 1: raise ValueError('Unexpected pinned client source: '+str(path))
    path.write_text(text.replace(before, after), encoding='utf-8', newline='\n')


def client():
    output = PROJECT/'outputs/metol/remote'
    output.mkdir(parents=True, exist_ok=True)
    web = output/'web_root'
    shutil.copytree(PROJECT/'work/reference/metol/scripts/nx-rc/web_root', web, dirs_exist_ok=True)
    change(web/'index.html', '<i class="fa fa-camera-retro"></i> NX-KS2',
           '<i class="fa fa-camera-retro" aria-hidden="true"></i> NX300M')
    change(web/'index.html', '<a class="dropdown-toggle" data-toggle="dropdown" href="#" role="button">',
           '<a class="dropdown-toggle" data-toggle="dropdown" href="#" role="button" aria-label="相机" title="相机">')
    # On narrow screens the visible text is hidden, so name the controls
    # independently of their icon-font glyphs.
    for icon, label in (('question-sign', '帮助'), ('cog', '设置')):
        before = ('href="#" role="button" aria-haspopup="true" aria-expanded="false">\n'
                  f'      <span class="glyphicon glyphicon-{icon}"></span>')
        after = (f'href="#" role="button" aria-label="{label}" title="{label}" aria-haspopup="true" aria-expanded="false">\n'
                 f'      <span class="glyphicon glyphicon-{icon}" aria-hidden="true"></span>')
        change(web/'index.html', before, after)
    for fragment, label in (('controller', '控制器'), ('gallery', '相册')):
        change(web/'index.html', f'<a href="#{fragment}" data-toggle="tab">',
               f'<a href="#{fragment}" data-toggle="tab" aria-label="{label}" title="{label}">')
    path = web/'js/app.js'
    text = path.read_text(encoding='utf-8')
    start = text.index('    this.hostname = document.location.hostname;')
    end = text.index('\n\n    this.controllers', start)
    path.write_text(text[:start]+'    this.hostname = document.location.host;'+text[end:], encoding='utf-8', newline='\n')
    path = web/'js/controller.js'
    change(path, 'self.macAddress = info.mac_address;', 'self.capabilities = info.capabilities || {};\n            self.macAddress = info.mac_address || info.instance_id || self.hostname;')
    change(path, "self.controlLcd('on');", "if (self.capabilities.lcd) self.controlLcd('on');")
    change(path, "return this.nxModelName == 'NX300';", "return this.nxModelName == 'NX300' || this.nxModelName == 'NX300M';")
    change(path, 'self.settings = new Settings(self.macAddress);', 'self.settings = new Settings(self.macAddress);\n            self.settings.capabilities = self.capabilities;')
    change(path, "    // Tab 页被浏览器挂起/已销毁时不发请求\n    if (self.destroyed) {", "    // Tab 页被浏览器挂起/已销毁时不发请求\n    if (self.destroyed || self.viewFinder == null) {")
    change(path, '            if (self.isNx1()) {', '''            if ((self.isNx1() && !(status.battery_percent >= 0)) ||
                (!self.isNx1() && !(status.battery_level >= 0))) {
                html += '<span class="badge-chip">电量未知</span>';
            } else if (self.isNx1()) {''')
    change(path, 'self.viewFinder = new ViewFinder(self);', '''self.viewFinder = new ViewFinder(self);
            [['LiveView','sensor_liveview'],['LCD','lcd']].forEach(function(item) {
                if (self.capabilities[item[1]] === false) {
                    var group = self.viewFinder.screenSelect.find('optgroup[label="'+item[0]+'"]');
                    group.attr('label', item[0]+'（未接通）').find('option').prop('disabled', true).prop('selected', false);
                }
            });
            self.viewFinder.screenSelect.selectpicker('refresh');''')
    change(web/'js/settings.js', 'Settings.prototype.getLiveView = function () {', 'Settings.prototype.getLiveView = function () {\n    if (this.capabilities && this.capabilities.sensor_liveview === false) return "hide";')
    change(web/'js/liveview.js', 'LiveView.prototype.start = function (low) {', 'LiveView.prototype.start = function (low) {\n    if (this.controller.capabilities.sensor_liveview === false) { this.target.hide(); return; }')
    font_root = PROJECT/'work/reference/bootstrap-3.3.7/fonts'
    shutil.copyfile(font_root/'glyphicons-halflings-regular.woff2', web/'fonts/glyphicons-halflings-regular.woff2')
    (web/'css/nx300m.css').write_text('@font-face{font-family:FontAwesome;src:url(../fonts/FontAwesome.otf) format("opentype");font-weight:normal;font-style:normal}\n@font-face{font-family:"Glyphicons Halflings";src:url(../fonts/glyphicons-halflings-regular.woff2) format("woff2")}\n', encoding='utf-8')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/nx300m.css">\n</head>')
    shutil.copyfile(PROJECT/'mod/metol/remote/gallery.js', web/'js/nx-gallery.js')
    shutil.copyfile(PROJECT/'mod/metol/remote/gallery.css', web/'css/nx-gallery.css')
    shutil.copyfile(PROJECT/'mod/metol/remote/quickshare.js', web/'js/quickshare.js')
    shutil.copyfile(PROJECT/'mod/metol/remote/quickshare.css', web/'css/quickshare.css')
    shutil.copyfile(PROJECT/'mod/metol/remote/exposure.js', web/'js/exposure.js')
    shutil.copyfile(PROJECT/'mod/metol/remote/exposure.css', web/'css/exposure.css')
    shutil.copyfile(PROJECT/'mod/metol/remote/looks.js', web/'js/looks.js')
    shutil.copyfile(PROJECT/'mod/metol/remote/looks.css', web/'css/looks.css')
    shutil.copyfile(PROJECT/'mod/metol/remote/recipe.js', web/'js/recipe.js')
    shutil.copyfile(PROJECT/'mod/metol/remote/recipe.css', web/'css/recipe.css')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/nx-gallery.css">\n  <script src="js/nx-gallery.js"></script>\n</head>')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/quickshare.css">\n  <script src="js/quickshare.js"></script>\n</head>')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/exposure.css">\n  <script src="js/exposure.js"></script>\n</head>')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/looks.css">\n  <script src="js/looks.js"></script>\n</head>')
    change(web/'index.html', '</head>', '  <link rel="stylesheet" href="css/recipe.css">\n  <script src="js/recipe.js"></script>\n</head>')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-only', action='store_true', help='只生成网页资源，不重编 ARM 后端')
    parser.add_argument('--backend-only', action='store_true', help='只重编 ARM 后端，保留已有网页资源')
    args = parser.parse_args()
    if args.client_only and args.backend_only: parser.error('请选择一种构建范围')
    if not args.backend_only: client()
    if args.client_only:
        print('Metol web client built')
        return
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
src=work/reference/nx-remote-controller/nx-remote-controller-daemon
mg=work/reference/nx-remote-controller/externals/mongoose
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O2 -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE -DDEBUG \
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -include mod/metol/remote/compat.h \
 -I"$tc/include" -I"$src" -I"$mg" -Iwork/reference/metol/test_server/nxui \
 -DMG_DISABLE_HTTP_DIGEST_AUTH -DMG_DISABLE_MQTT -DMG_DISABLE_MD5 -DMG_DISABLE_JSON_RPC -DMG_DISABLE_SOCKETPAIR \
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 mod/metol/remote/remote.c mod/metol/remote/compat.c "$src/nx_model.c" "$src/status.c" "$src/util.c" "$src/command.c" "$mg/mongoose.c" \
 work/reference/metol/test_server/nxui/nxui_model.c \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" \
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libc-2.13.so" -lgcc \
 -o outputs/metol/remote/nx-remote-controller.arm
"$tc/bin/arm-linux-gnueabi-readelf" -V outputs/metol/remote/nx-remote-controller.arm > outputs/metol/remote/versions.txt
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, encoding='utf-8', errors='replace')
    (PROJECT/'outputs/metol/remote/build.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    if result.returncode: raise RuntimeError(f'ARM remote build exited {result.returncode}:\n'+result.stderr[-6000:])
    print('NX300M ARM remote backend built' if args.backend_only else 'NX300M ARM remote backend and Metol web client built')


if __name__ == '__main__': main()
