"""Generate NXConnect's vector adaptive icon and Android 7 fallback."""
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "mod/nxconnect/NXConnect/app/src/main/res"
BODY = "M33,36 L77,36 Q83,36 83,42 L83,68 Q83,74 77,74 L32,74 Q25,74 25,67 L25,44 Q25,36 33,36 Z"
DIAL = "M30,32 L39,32 Q41,32 41,34 L41,36 L28,36 L28,34 Q28,32 30,32 Z"
SHUTTER = "M44,33 L48,33 Q50,33 50,35 L50,36 L42,36 L42,35 Q42,33 44,33 Z"
# The taller body contains the circular lens, so themed icons use the same outline.
SILHOUETTE = BODY
GRADIENT = '<gradient android:angle="270" android:startColor="#FF8291" android:centerColor="#F9657D" android:endColor="#EE4266" />'
HEADER = '<?xml version="1.0" encoding="utf-8"?>\n'
VECTOR = ('<vector xmlns:android="http://schemas.android.com/apk/res/android" '
          'xmlns:aapt="http://schemas.android.com/aapt" android:width="108dp" '
          'android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">\n')


def path(data, color):
    return f'    <path android:fillColor="{color}" android:pathData="{data}" />\n'


def circle(x, y, radius):
    return f'M{x},{y-radius} A{radius},{radius} 0,1 0,{x} {y+radius} A{radius},{radius} 0,1 0,{x} {y-radius} Z'


def shaded(data, start, end, colors):
    middle = f' android:centerColor="{colors[1]}"' if len(colors) == 3 else ''
    return (f'    <path android:pathData="{data}">\n'
            '        <aapt:attr name="android:fillColor">\n'
            f'            <gradient android:startX="{start[0]}" android:startY="{start[1]}" '
            f'android:endX="{end[0]}" android:endY="{end[1]}" android:startColor="{colors[0]}"'
            f'{middle} android:endColor="{colors[-1]}" android:type="linear" />\n'
            '        </aapt:attr>\n    </path>\n')


def write(name, text):
    target = RES / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(HEADER + text, encoding="utf-8")


def main():
    write("drawable/ic_launcher_background.xml",
          '<!-- Full bleed; the launcher applies the outer mask. -->\n'
          '<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">\n'
          f'    {GRADIENT}\n</shape>\n')
    foreground = (VECTOR + '    <group android:translateY="1.5">\n' + path(SILHOUETTE, "#28A3264A") + '    </group>\n'
                  + shaded(DIAL, (34, 32), (34, 36), ["#F5F7FB", "#A8B2C3"])
                  + path(SHUTTER, "#3B4352")
                  + shaded(BODY, (54, 36), (54, 74), ["#FFFFFF", "#DCE2EB"])
                  + shaded("M25,46 L83,46 L83,68 Q83,71 79,71 L31,71 Q25,71 25,66 Z",
                           (25, 48), (83, 70), ["#424B59", "#272E3B"])
                  + path("M25,47 Q25,44 29,44 L35,44 Q40,44 40,50 L40,71 L31,71 Q25,71 25,66 Z", "#343C49")
                  + path(circle(46, 41, 1.6), "#858F9E")
                  + path(circle(61, 55, 18), "#C5CDD9")
                  + shaded(circle(61, 55, 16.6), (51, 39), (70, 71), ["#455064", "#202838"])
                  + path(circle(61, 55, 13.8), "#172135")
                  + shaded(circle(61, 55, 10.8), (54, 46), (68, 64), ["#759FC5", "#3C5D82", "#23334F"])
                  + path(circle(61, 55, 6.7), "#283D59")
                  + path(circle(57.5, 50.9, 2.7), "#55D6EBFF")
                  + '</vector>\n')
    write("drawable/ic_launcher_foreground.xml", '<!-- Central artwork fits the adaptive icon safe region. -->\n' + foreground)
    write("drawable/ic_launcher_monochrome.xml", VECTOR
          + path(DIAL, "#FFFFFF") + path(SHUTTER, "#FFFFFF")
          + f'    <path android:fillColor="#FFFFFF" android:fillType="evenOdd" android:pathData="{SILHOUETTE} {circle(61, 55, 12.5)}" />\n'
          + path(circle(61, 55, 8), "#FFFFFF") + '</vector>\n')
    for name in ("ic_launcher", "ic_launcher_round"):
        write(f"mipmap-anydpi-v26/{name}.xml",
              '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
              '    <background android:drawable="@drawable/ic_launcher_background" />\n'
              '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
              '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
              '</adaptive-icon>\n')
        shape = 'oval' if name.endswith("round") else 'rectangle'
        corners = '            <corners android:radius="22dp" />\n' if shape == 'rectangle' else ''
        write(f"mipmap-anydpi/{name}.xml",
              '<!-- Android 7.x fallback; newer launchers use the adaptive layers. -->\n'
              '<layer-list xmlns:android="http://schemas.android.com/apk/res/android">\n'
              '    <item android:left="3dp" android:top="3dp" android:right="3dp" android:bottom="3dp">\n'
              f'        <shape android:shape="{shape}">\n{corners}            {GRADIENT}\n        </shape>\n    </item>\n'
              '    <item android:drawable="@drawable/ic_launcher_foreground" />\n</layer-list>\n')
        for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
            (RES / f"mipmap-{density}/{name}.png").unlink(missing_ok=True)
    (RES / "drawable/ic_launcher_foreground.png").unlink(missing_ok=True)
    print("Generated independent background, foreground, monochrome and legacy vector icons.")


if __name__ == "__main__":
    main()
